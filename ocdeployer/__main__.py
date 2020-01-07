#!/usr/bin/env python
from __future__ import print_function

import appdirs
import click
import logging
import os
import pathlib
import json
import subprocess
import sys
import re
import shutil

import prompter
import yaml

from ocdeployer.utils import (
    all_sets,
    oc,
    get_routes,
    switch_to_project,
    get_server_info,
)
from ocdeployer.secrets import SecretImporter
from ocdeployer.deploy import DeployRunner
from ocdeployer.env import EnvConfigHandler, LegacyEnvConfigHandler
from ocdeployer.events import start_event_watcher


log = logging.getLogger("ocdeployer")
logging.basicConfig(level=logging.INFO)
logging.getLogger("sh").setLevel(logging.CRITICAL)
appdirs_path = pathlib.Path(appdirs.user_cache_dir(appname="ocdeployer"))

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def wipe(no_confirm, project, label):
    server = get_server_info()
    extra_msg = ""
    if label:
        extra_msg = " with label '{}'".format(label)

    if not no_confirm and prompter.yesno(
        "I'm about to delete everything in project '{}'{} on server {} -- continue?".format(
            project, extra_msg, server
        ),
        default="no",
    ):
        sys.exit(0)

    switch_to_project(project)

    if label:
        args = ["--selector", label]
    else:
        args = ["--all"]

    oc("delete", "all", *args, _exit_on_err=False)
    oc("delete", "configmap", *args, _exit_on_err=False)
    oc("delete", "secret", *args, _exit_on_err=False)
    oc("delete", "pvc", *args, _exit_on_err=False)


def list_routes(project, output=None):
    switch_to_project(project)
    route_data = get_routes()

    if not output:
        log.info("The following routes now exist:")
        max_len = 10
        for svc_name in route_data:
            if len(svc_name) > max_len:
                max_len = len(svc_name)
        for svc_name, svc_route in route_data.items():
            log.info("%-*s %s", max_len, svc_name, svc_route)

    elif output == "json":
        print(json.dumps(route_data, indent=2))

    elif output == "yaml":
        print(yaml.dump(route_data, default_flow_style=False))


def list_sets(template_dir, output=None):
    as_dict = {"service_sets": all_sets(template_dir)}

    if not output:
        log.info("Available service sets:\n * %s", "\n * ".join(as_dict["service_sets"]))

    elif output == "json":
        print(json.dumps(as_dict, indent=2))

    elif output == "yaml":
        print(yaml.dump(as_dict, default_flow_style=False))


def verify_label(label):
    if not label:
        return
    if not re.match(r"^\w+=\w+$", label):
        log.error("Label '%s' is not valid.  Example: 'mylabel=myvalue'", label)
        sys.exit(1)


@click.group(
    help="Deploys components to a given cluster. NOTE: You need the openshift cli tool"
    " ('oc') installed and to login to your openshift cluster before running the tool.",
    context_settings=CONTEXT_SETTINGS,
)
def main():
    """Main ocdeployer group"""
    pass


# Options shared by both the "deploy" command and the "process" command
_common_options = [
    click.option("--all", "-a", "all_services", is_flag=True, help="Deploy all service sets"),
    click.option(
        "--sets", "-s", help="Comma,separated,list of specific service set names to deploy"
    ),
    click.option(
        "--pick",
        "-p",
        default=None,
        help="Pick a single component from a service set and deploy that.  E.g. '-p myset/myvm'",
    ),
    click.option("--skip", "-k", help="Comma,separated,list of service_set/service_name to skip"),
    click.option(
        "--env",
        "-e",
        "env_values",
        help=(
            "Name of environment to load variables from (default: None).  Use this option multiple"
            " times to concatenate environment configurations. The env listed first takes priority."
            "  You can also specify filenames here (see docs on legacy env file processing)."
        ),
        multiple=True,
    ),
    click.option(
        "--env-dir-name",
        "-n",
        default="env",
        help=(
            "Env variables directory name (default 'env').  This is joined"
            " to $WORKING_DIR & each service set dir to set path for discovering env files.  NOTE:"
            " Does not apply to legacy env file processing."
        ),
    ),
    click.option(
        "--template-dir", "-t", default=None, help="Template directory (default 'templates')"
    ),
    click.option(
        "--scale-resources",
        type=float,
        default=1.0,
        help="Factor to scale configured cpu/memory resource requests/limits by",
    ),
]


def common_options(func):
    """Click decorator used for common options, shared by deploy and process commands."""
    for option in reversed(_common_options):
        func = option(func)
    return func


def output_option(func):
    """Click decorator used for output option, shared by several commands."""
    option = click.option(
        "--output",
        "-o",
        default=None,
        type=click.Choice(["yaml", "json"]),
        help="Output data using yaml or json format",
    )
    return option(func)


def _parse_args(template_dir, env_values, env_dir_name, all_services, sets, pick, dst_project):
    """Parses args common to 'process' and 'deploy'."""
    if not template_dir:
        path = appdirs_path / "templates"
        template_dir = path if path.exists() else pathlib.Path(pathlib.os.getcwd()) / "templates"

    template_dir = os.path.abspath(template_dir)

    # Analyze the values provided by --env to determine which config handler we are using
    all_env_values_are_files = all([os.path.exists(value) for value in env_values])
    some_env_values_are_files = any([os.path.exists(value) for value in env_values])
    if all_env_values_are_files:
        log.info("A specific filename was provided for env, using legacy env file processing")
        env_config_handler = LegacyEnvConfigHandler(env_files=env_values)
    elif some_env_values_are_files:
        log.error(
            "Error: Values for '--env' must be either all valid filenames, or all text env names"
        )
        sys.exit(1)
    else:
        env_config_handler = EnvConfigHandler(env_names=env_values, env_dir_name=env_dir_name)

    if not all_services and not sets and not pick:
        log.error(
            "Error: no service sets or components selected for deploy."
            " Use --sets, --all, or --pick"
        )
        sys.exit(1)

    specific_component = None
    server = get_server_info()

    if pick:
        try:
            service_set, specific_component = pick.split("/")
        except ValueError:
            log.error("Invalid format for '--pick', use: 'service_set/component'")
            sys.exit(1)
        sets_selected = [service_set]
        confirm_msg = (
            "Deploying single component '{}' to project '{}' on server {} -- continue?"
        ).format(pick, dst_project, server)
    else:
        if all_services:
            sets_selected = all_sets(template_dir)
        else:
            sets_selected = sets.split(",")
        confirm_msg = (
            "Deploying service sets '{}' to project '{}' on server {} -- continue?"
        ).format(", ".join(sets_selected), dst_project, server)

    return template_dir, env_config_handler, specific_component, sets_selected, confirm_msg


@main.command("process", help="Process templates but do not deploy")
@common_options
@output_option
@click.option(
    "--to-dir",
    default=None,
    help="Save processed templates to specific output directory (default: print to stdout)",
)
@click.argument("dst_project")
def deploy_dry_run(
    dst_project,
    sets,
    all_services,
    env_values,
    template_dir,
    env_dir_name,
    scale_resources,
    pick,
    skip,
    output,
    to_dir,
):
    template_dir, env_config_handler, specific_component, sets_selected, _ = _parse_args(
        template_dir, env_values, env_dir_name, all_services, sets, pick, dst_project
    )

    # No need to set up SecretImporter, it won't be used in a dry run

    DeployRunner(
        template_dir,
        dst_project,
        env_config_handler,
        ignore_requires=True,  # ignore for a dry run
        service_sets_selected=sets_selected,
        resources_scale_factor=scale_resources,
        custom_dir=None,  # won't be used in a dry run
        specific_component=specific_component,
        label=None,
        skip=skip.split(",") if skip else None,
        dry_run=True,
        dry_run_opts={"output": output, "to_dir": to_dir},
    ).run()


@main.command("deploy", help="Deploy to project")
@common_options
@click.option("--no-confirm", "-f", is_flag=True, help="Do not prompt for confirmation")
@click.option(
    "--secrets-local-dir",
    default=None,
    help="Import secrets from local files in a directory (default 'secrets')",
)
@click.option(
    "--secrets-src-project",
    default="secrets",
    help="Openshift project to import secrets from (default: secrets)",
)
@click.option(
    "--ignore-requires",
    "-i",
    is_flag=True,
    help="Ignore the 'requires' statement in config files and deploy anyway",
)
@click.option(
    "--custom-dir",
    "-u",
    default=None,
    help="Specify custom deploy scripts directory (default 'custom')",
)
@click.option(
    "--label",
    "-l",
    default=None,
    help="Adds a label to each deployed resource.  E.g. '-l app=test'",
)
@click.option(
    "--watch", "-w", is_flag=True, default=False, help="Enable event watching during the deploy"
)
@click.argument("dst_project")
def deploy_to_project(
    dst_project,
    no_confirm,
    secrets_local_dir,
    sets,
    all_services,
    secrets_src_project,
    env_values,
    template_dir,
    env_dir_name,
    ignore_requires,
    scale_resources,
    custom_dir,
    pick,
    label,
    skip,
    watch,
):
    if not custom_dir:
        path = appdirs_path / "custom"
        custom_dir = path if path.exists() else pathlib.Path(pathlib.os.getcwd()) / "custom"

    if not secrets_local_dir:
        path = appdirs_path / "secrets"
        secrets_local_dir = path if path.exists() else pathlib.Path(pathlib.os.getcwd()) / "secrets"

    if not dst_project:
        log.error("Error: no destination project given")
        sys.exit(1)

    verify_label(label)

    SecretImporter.local_dir = secrets_local_dir
    SecretImporter.source_project = secrets_src_project

    template_dir, env_config_handler, specific_component, sets_selected, confirm_msg = _parse_args(
        template_dir, env_values, env_dir_name, all_services, sets, pick, dst_project
    )

    if not no_confirm and not prompter.yesno(confirm_msg):
        log.info("Aborted by user")
        sys.exit(0)

    switch_to_project(dst_project)

    if watch:
        event_watcher = start_event_watcher(dst_project)

    DeployRunner(
        template_dir,
        dst_project,
        env_config_handler,
        ignore_requires=ignore_requires,
        service_sets_selected=sets_selected,
        resources_scale_factor=scale_resources,
        custom_dir=custom_dir,
        specific_component=specific_component,
        label=label,
        skip=skip.split(",") if skip else None,
        dry_run=False,
    ).run()

    if watch and event_watcher:
        event_watcher.stop()

    list_routes(dst_project)


@main.command("wipe", help="Delete everything from project")
@click.option("--no-confirm", "-f", is_flag=True, help="Do not prompt for confirmation")
@click.option(
    "--label", "-l", default=None, help="Delete only a specific label.  E.g. '-l app=test'"
)
@click.argument("dst_project")
def wipe_project(no_confirm, dst_project, label):
    verify_label(label)
    return wipe(no_confirm, dst_project, label)


@main.command("list-routes", help="List routes currently in the project")
@click.argument("dst_project")
@output_option
def list_act_routes(dst_project, output):
    return list_routes(dst_project, output)


@main.command("list-sets", help="List service sets available in template dir")
@click.option("--template-dir", "-t", default=None, help="Template directory (default 'templates')")
@output_option
def list_act_sets(template_dir, output):
    if template_dir is None:
        path = appdirs_path / "templates"
        template_dir = path if path.exists() else pathlib.Path(pathlib.os.getcwd()) / "templates"
    return list_sets(template_dir, output)


@main.group("cache")
def cache():
    """Used for updating or deleting local template cache"""
    pass


@cache.command("initialize", help="Fetch new template cache")
@click.option(
    "--install-dir",
    "-i",
    default=appdirs_path,
    help="Location to store cached templates and configs",
)
@click.argument("url")
def initialize_cache(install_dir, url):
    if not install_dir.exists():
        proc = subprocess.Popen(["git", "clone", url, str(install_dir)])
        proc.wait()
    else:
        print(
            f"{install_dir} already exists, use --update to update files"
            f" or --delete to clear current cache"
        )


@cache.command("update", help="Update template cache files")
@click.option(
    "--install-dir",
    "-i",
    default=appdirs_path,
    help="Location to store cached templates and configs",
)
def update_cache(install_dir):
    my_env = os.environ.copy()
    my_env["GIT_WORK_TREE"] = str(install_dir)
    git_dir = install_dir / ".git"

    args = ["git", "--git-dir", str(git_dir), "pull", "origin", "master"]
    proc = subprocess.Popen(args, env=my_env)
    proc.wait()


@cache.command("delete", help="Delete current template cache")
@click.option(
    "--install-dir",
    "-i",
    default=appdirs_path,
    help="Location to store cached templates and configs",
)
def delete_cache(install_dir):
    if not install_dir.exists():
        print(f"{install_dir} already deleted please use initialize to create new cache")
    else:
        click.confirm(f"Are you sure you want to delete {install_dir}?", abort=True)
        shutil.rmtree(install_dir)


if __name__ == "__main__":
    main()
