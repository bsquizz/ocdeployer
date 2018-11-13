#!/usr/bin/env python
from __future__ import print_function

import click
import logging
import os
import json
import sys
import re

import prompter
import yaml

from ocdeployer.utils import oc, load_cfg_file, get_routes, switch_to_project
from ocdeployer.secrets import SecretImporter
from ocdeployer.deploy import DeployRunner


log = logging.getLogger("ocdeployer")
logging.basicConfig(level=logging.INFO)
logging.getLogger("sh").setLevel(logging.CRITICAL)

CONTEXT_SETTINGS = dict(help_option_names=["-h", "--help"])


def wipe(no_confirm, project, label):
    extra_msg = ""
    if label:
        extra_msg = " with label '{}'".format(label)

    if not no_confirm and prompter.yesno(
        "I'm about to delete everything in project '{}'{}.  Continue?".format(
            project, extra_msg
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


def all_sets(template_dir):
    try:
        walk = next(os.walk(template_dir))
    except StopIteration:
        log.error("Error: template dir '%s' invalid", template_dir)
        sys.exit(1)

    return walk[1]


def list_sets(template_dir, output=None):
    as_dict = {"service_sets": all_sets(template_dir)}

    if not output:
        log.info("Available service sets: %s", as_dict["service_sets"])

    elif output == "json":
        print(json.dumps(as_dict, indent=2))

    elif output == "yaml":
        print(yaml.dump(as_dict, default_flow_style=False))


def get_variables_data(variables_file):
    variables_data = load_cfg_file(variables_file)

    # Check if there's any variables we need to prompt for
    for section, data in variables_data.items():
        for var_name, var_value in data.items():
            if var_value == "{prompt}":
                variables_data[section][var_name] = prompter.prompt(
                    "Enter value for parameter '{}' in section '{}':".format(
                        var_name, section
                    )
                )

    return variables_data


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


@main.command("deploy", help="Deploy to project")
@click.option("--no-confirm", "-f", is_flag=True, help="Do not prompt for confirmation")
@click.option(
    "--secrets-local-dir",
    default=os.path.join(os.getcwd(), "secrets"),
    help="Import secrets from local files in a directory (default ./secrets)",
)
@click.option(
    "--sets", "-s", help="Comma,separated,list of specific service set names to deploy"
)
@click.option(
    "--all", "-a", "all_services", is_flag=True, help="Deploy all service sets"
)
@click.option(
    "--secrets-src-project",
    default="secrets",
    help="Openshift project to import secrets from (default: secrets)",
)
@click.option(
    "--env-file",
    "-e",
    default="",
    help="Path to parameters config file (default: None)",
)
@click.option(
    "--template-dir",
    "-t",
    default=os.path.join(os.getcwd(), "templates"),
    help="Template directory (default ./templates)",
)
@click.option(
    "--ignore-requires",
    "-i",
    help="Ignore the 'requires' statement in config files and deploy anyway",
)
@click.option(
    "--scale-resources",
    type=float,
    default=1.0,
    help="Factor to scale configured cpu/memory resource requests/limits by",
)
@click.option(
    "--custom-dir",
    "-u",
    default=os.path.join(os.getcwd(), "custom"),
    help="Custom deploy scripts directory (default ./custom)",
)
@click.option(
    "--pick",
    "-p",
    default=None,
    help="Pick a single component from a service"
    " set and deploy that.  E.g. '-p myset/myvm'",
)
@click.option(
    "--label",
    "-l",
    default=None,
    help="Adds a label to each deployed resource.  E.g. '-l app=test'",
)
@click.argument("dst_project")
def deploy_to_project(
    dst_project,
    no_confirm,
    secrets_local_dir,
    sets,
    all_services,
    secrets_src_project,
    env_file,
    template_dir,
    ignore_requires,
    scale_resources,
    custom_dir,
    pick,
    label,
):

    if not dst_project:
        log.error("Error: no destination project given")
        sys.exit(1)

    verify_label(label)

    SecretImporter.local_dir = secrets_local_dir
    SecretImporter.source_project = secrets_src_project

    template_dir = os.path.abspath(template_dir)

    if not all_services and not sets and not pick:
        log.error(
            "Error: no service sets or components selected for deploy."
            " Use --sets, --all, or --pick"
        )
        sys.exit(1)

    specific_component = None

    if pick:
        try:
            service_set, specific_component = pick.split("/")
        except ValueError:
            log.error("Invalid format for '--pick', use: 'service_set/component'")
            sys.exit(1)
        sets_selected = [service_set]
        confirm_msg = "Deploying single component '{}' to project '{}'.  Continue?".format(
            pick, dst_project
        )
    else:
        if all_services:
            sets_selected = all_sets(template_dir)
        else:
            sets_selected = sets.split(",")
        confirm_msg = "Deploying service sets '{}' to project '{}'.  Continue?".format(
            ", ".join(sets_selected), dst_project
        )

    if not no_confirm and not prompter.yesno(confirm_msg):
        log.info("Aborted by user")
        sys.exit(0)

    if env_file:
        variables_data = get_variables_data(env_file)
    else:
        variables_data = {}

    switch_to_project(dst_project)

    DeployRunner(
        template_dir,
        dst_project,
        variables_data,
        ignore_requires=ignore_requires,
        service_sets_selected=sets_selected,
        resources_scale_factor=scale_resources,
        custom_dir=custom_dir,
        specific_component=specific_component,
        label=label,
    ).run()

    list_routes(dst_project)


@main.command("wipe", help="Delete everything from project")
@click.option("--no-confirm", "-f", is_flag=True, help="Do not prompt for confirmation")
@click.option(
    "--label",
    "-l",
    default=None,
    help="Delete only a specific label.  E.g. '-l app=test'",
)
@click.argument("dst_project")
def wipe_project(no_confirm, dst_project, label):
    verify_label(label)
    return wipe(no_confirm, dst_project, label)


@main.command("list-routes", help="List routes currently in the project")
@click.argument("dst_project")
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Choice(["yaml", "json"]),
    help="When listing parameters, print output in yaml or json format",
)
def list_act_routes(dst_project, output):
    return list_routes(dst_project, output)


@main.command("list-sets", help="List service sets available in template dir")
@click.option(
    "--template-dir",
    "-t",
    default=os.path.join(os.getcwd(), "templates"),
    help="Template directory (default ./templates)",
)
@click.option(
    "--output",
    "-o",
    default=None,
    type=click.Choice(["yaml", "json"]),
    help="When listing parameters, print output in yaml or json format",
)
def list_act_sets(template_dir, output):
    return list_sets(template_dir, output)


if __name__ == "__main__":
    main()
