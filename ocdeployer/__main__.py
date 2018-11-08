#!/usr/bin/env python
from __future__ import print_function

import argparse
import logging
import os
import json
import sys

import prompter
import yaml

from ocdeployer.utils import oc, load_cfg_file, get_routes, get_cfg_files_in_dir, switch_to_project
from ocdeployer.secrets import SecretImporter
from ocdeployer.deploy import DeployRunner


log = logging.getLogger("ocdeployer")


def wipe(no_confirm, project):
    if not no_confirm and prompter.yesno(
        "I'm about to delete everything in project '{}'.  Continue?".format(project),
        default="no",
    ):
        sys.exit(0)

    switch_to_project(project)
    oc("delete", "all", "--all", _exit_on_err=False)
    oc("delete", "configmap", "--all", _exit_on_err=False)
    oc("delete", "secret", "--all", _exit_on_err=False)
    oc("delete", "pvc", "--all", _exit_on_err=False)


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


def list_sets(all_sets, output=None):
    as_dict = {'service_sets': all_sets}

    if not output:
        log.info("Available service sets: %s", as_dict['service_sets'])

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


def main(args):
    if not args.dst_project and not args.list_sets:
        log.error("Error: no destination project given")
        sys.exit(1)

    if args.wipe:
        return wipe(args.no_confirm, args.dst_project)

    if args.list_routes:
        return list_routes(args.dst_project, args.output)

    SecretImporter.local_dir = args.secrets_local_dir
    SecretImporter.source_project = args.secrets_src_project

    template_dir = os.path.abspath(args.template_dir)

    try:
        walk = next(os.walk(template_dir))
    except StopIteration:
        log.error("Error: template dir '%s' invalid", template_dir)
        sys.exit(1)

    all_sets = walk[1]

    if args.list_sets:
        return list_sets(all_sets, args.output)

    if not args.all and not args.sets and not args.pick:
        log.error("Error: no service sets or components selected for deploy.  Use --sets, --all, or --pick")
        sys.exit(1)

    specific_component = None

    if args.pick:
        try:
            service_set, specific_component = args.pick.split("/")
        except ValueError:
            log.error("Invalid format for '--pick', use: 'service_set/component'")
            sys.exit(1)
        sets_selected = [service_set]
        confirm_msg = "Deploying single component '{}' to project '{}'.  Continue?".format(
            args.pick, args.dst_project
        )
    else:
        if args.all:
            sets_selected = all_sets
        else:
            sets_selected = args.sets.split(",")
        confirm_msg = "Deploying service sets '{}' to project '{}'.  Continue?".format(
                ", ".join(sets_selected), args.dst_project
            )

    if not args.no_confirm and not prompter.yesno(confirm_msg):
        log.info("Aborted by user")
        sys.exit(0)

    if args.env_file:
        variables_data = get_variables_data(args.env_file)
    else:
        variables_data = {}

    switch_to_project(args.dst_project)

    DeployRunner(
        template_dir,
        args.dst_project,
        variables_data,
        ignore_requires=args.ignore_requires,
        service_sets_selected=sets_selected,
        resources_scale_factor=args.scale_resources,
        custom_dir=args.custom_dir,
        specific_component=specific_component,
    ).run()

    list_routes(args.dst_project)


def cli():

    logging.basicConfig(level=logging.INFO)
    logging.getLogger("sh").setLevel(logging.CRITICAL)

    parser = argparse.ArgumentParser(description="Deploy Tool")
    parser.add_argument(
        "--no-confirm", "-f", action="store_true", help="Do not prompt for confirmation"
    )
    parser.add_argument(
        "--secrets-local-dir",
        type=str,
        default=os.path.join(os.getcwd(), "secrets"),
        help="Import secrets from local files in a directory (default ./secrets)",
    )
    parser.add_argument(
        "--secrets-src-project",
        type=str,
        default="secrets",
        help="Openshift project to import secrets from (default: secrets)",
    )
    parser.add_argument(
        "--all", "-a", action="store_true", help="Deploy all service sets"
    )
    parser.add_argument(
        "--sets",
        "-s",
        type=str,
        help="Comma,separated,list of specific service set names to deploy",
    )
    parser.add_argument(
        "dst_project", type=str, nargs="?", help="Destination project to deploy to"
    )
    parser.add_argument(
        "--env-file",
        "-e",
        default="",
        type=str,
        help="Path to parameters config file (default: None)",
    )
    parser.add_argument(
        "--template-dir",
        "-t",
        type=str,
        default=os.path.join(os.getcwd(), "templates"),
        help="Template directory (default ./templates)",
    )
    parser.add_argument(
        "--ignore-requires",
        "-i",
        action="store_true",
        help="Ignore the 'requires' statement in config files and deploy anyway",
    )
    parser.add_argument(
        "--scale-resources",
        type=float,
        default=1.0,
        help="Factor to scale configured cpu/memory resource requests/limits by",
    )
    parser.add_argument(
        "--custom-dir",
        "-u",
        type=str,
        default=os.path.join(os.getcwd(), "custom"),
        help="Custom deploy scripts directory (default ./custom)",
    )
    parser.add_argument(
        "--wipe",
        "-w",
        action="store_true",
        help="Wipe the project (delete EVERYTHING in it)",
    )
    parser.add_argument(
        "--list-routes",
        "-r",
        action="store_true",
        help="List the routes currently configured in the project and exit",
    )
    parser.add_argument(
        "--list-sets",
        "-l",
        action="store_true",
        help="List service sets available to select in the template dir and exit",
    )
    parser.add_argument(
        "--output",
        "-o",
        default=None,
        choices=["yaml", "json"],
        help="When using --list-* parameters, print output in yaml or json format"
    )
    parser.add_argument(
        "--pick",
        "-p",
        default=None,
        type=str,
        help="Pick a single component from a service set and deploy that.  E.g. '-p myset/myvm'"
    )
    args = parser.parse_args()
    main(args)


if __name__ == "__main__":
    cli()
