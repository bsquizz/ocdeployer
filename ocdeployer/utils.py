from __future__ import print_function

import glob
import json
import logging
import sys
import threading
import time
import os
import yaml

import sh
from sh import ErrorReturnCode


log = logging.getLogger(__name__)


# Resource types and their cli shortcuts
# Mostly listed here: https://docs.openshift.com/online/cli_reference/basic_cli_operations.html
SHORTCUTS = {
    "build": None,
    "buildconfig": "bc",
    "deploymentconfig": "dc",
    "event": "ev",
    "imagestream": "is",
    "imagestreamtag": "istag",
    "imagestreamimage": "isimage",
    "job": None,
    "limitrange": "limits",
    "node": None,
    "pod": "po",
    "resourcequota": "quota",
    "replicationcontroller": "rc",
    "secrets": None,
    "service": "svc",
    "serviceaccount": "sa",
    "persistentvolume": "pv",
    "persistentvolumeclaim": "pvc",
    "configmap": None,
    "route": None,
}


def parse_restype(string):
    """
    Given a resource type or its shortcut, return the full resource type name.
    """
    string_lower = string.lower()
    if string_lower in SHORTCUTS:
        return string_lower

    for resource_name, shortcut in SHORTCUTS.items():
        if string_lower == shortcut:
            return resource_name

    raise ValueError("Unknown resource type: {}".format(string))


def get_cfg_files_in_dir(path):
    """
    Get a list of all .yml/.json files in a dir

    Ignore the special _cfg file
    """
    files = list(glob.glob(os.path.join(path, "*.yaml")))
    files.extend(list(glob.glob(os.path.join(path, "*.yml"))))
    files.extend(list(glob.glob(os.path.join(path, "*.json"))))
    return [f for f in files if not os.path.basename(f).startswith("_cfg")]


def load_cfg_file(path):
    if not os.path.isfile(path):
        raise ValueError("Path '{}' is not a file or does not exist".format(path))

    _, file_ext = os.path.splitext(path)

    with open(path, "rb") as f:
        if file_ext == ".yaml" or file_ext == ".yml":
            content = yaml.safe_load(f)
        elif file_ext == ".json":
            content = json.load(f)
        else:
            raise ValueError("File '{}' must be a YAML or JSON file".format(path))

    if not content:
        raise ValueError("File '{}' is empty!".format(path))

    return content


def oc(*args, **kwargs):
    """
    Run 'sh.oc' and print the command, show output, catch errors, etc.

    Optional kwargs:
        _reraise: if ErrorReturnCode is hit, don't exit, re-raise it
        _exit_on_err: sys.exit(1) if this command fails (default True)
        _silent: don't print command or output (default False)
        _ignore_immutable: ignore errors related to immutable objects (default True) 

    Returns:
        None if cmd fails and _exit_on_err is False
        command output (str) if command succeeds
    """
    _exit_on_err = kwargs.pop("_exit_on_err", True)
    _silent = kwargs.pop("_silent", False)
    _reraise = kwargs.pop("_reraise", False)
    _ignore_immutable = kwargs.pop("_ignore_immutable", True)

    kwargs["_bg_exc"] = True

    # Format the cmd args/kwargs for log printing before the command is run
    # Maybe 'sh' provides an easy way to do this...?
    cmd_args = " ".join([str(arg) for arg in args if arg is not None])

    cmd_kwargs = []
    for key, val in kwargs.items():
        if key.startswith("_"):
            continue
        if len(key) > 1:
            cmd_kwargs.append("--{} {}".format(key, val))
        else:
            cmd_kwargs.append("-{} {}".format(key, val))
    cmd_kwargs = " ".join(cmd_kwargs)

    if not _silent:
        log.info("Running command: oc %s %s", cmd_args, cmd_kwargs)

    err_lines = []

    def _err_line_handler(line):
        log.info("|  stderr  |%s", line.rstrip())
        err_lines.append(line)

    def _out_line_handler(line):
        log.info("|  stdout  |%s", line.rstrip())

    try:
        if _silent:
            output = sh.oc(*args, **kwargs).wait()
        else:
            output = sh.oc(
                *args, **kwargs, _out=_out_line_handler, _err=_err_line_handler
            ).wait()
        return output
    except ErrorReturnCode as err:
        immutable_errors_only = all(
            "field is immutable after creation" in line for line in err_lines
        )
        if immutable_errors_only and _ignore_immutable:
            log.warning("Ignoring immutable field errors")
        elif _reraise:
            raise
        elif _exit_on_err:
            log.error("Command failed!  Aborting.")
            sys.exit(1)
        else:
            log.warning("Non-zero return code ignored")


def switch_to_project(project):
    try:
        oc("get", "project", project, _reraise=True)
    except ErrorReturnCode as err:
        log.error("Unable to get project '%s', trying to create it...", project)
        oc("new-project", project, _exit_on_err=True)
    oc("project", project, _exit_on_err=True)


def get_json(restype, name=None):
    """
    Run 'oc get' for a given resource type/name and return the json output.

    If name is None all resources of this type are returned
    """
    restype = parse_restype(restype)

    args = ("get", restype)
    if name:
        args = ("get", restype, name)
    try:
        output = oc(*args, o="json", _exit_on_err=False, _silent=True)
    except ErrorReturnCode as err:
        if "NotFound" in err.stderr:
            return {}

    try:
        parsed_json = json.loads(str(output))
    except ValueError:
        return {}

    return parsed_json


def rollout(dc_name):
    """Rollout a deployment, wait for new revision to start deploying, wait for it to go active."""

    def _get_revision():
        try:
            return get_json("dc", dc_name)["status"]["latestVersion"]
        except KeyError:
            return "error"

    old_revision = _get_revision()
    try:
        oc("rollout", "latest", "dc/{}".format(dc_name), _reraise=True)
    except ErrorReturnCode as err:
        if "is already in progress" in str(err):
            pass
    else:
        # Wait for the new revision to start deploying
        for _ in range(0, 60):
            if _get_revision() != old_revision:
                break
            log.info("Waiting for rollout on dc/%s to begin", dc_name)
            time.sleep(1)

    wait_for_ready("dc", dc_name)


def get_routes():
    """
    Get all routes in the project.

    Return dict with key of service name, value of http route
    """
    data = get_json("route")
    ret = {}
    for route in data.get("items", []):
        ret[route["metadata"]["name"]] = route["spec"]["host"]
    return ret


def _check_status_for_restype(restype, json_data):
    """
    Depending on the resource type, check that it is "ready" or "complete"

    Uses the status json from an 'oc get'

    Returns True if ready, False if not.
    """
    try:
        status = json_data["status"]
    except KeyError:
        status = None

    if not status:
        return False

    restype = parse_restype(restype)

    if restype == "deploymentconfig":
        spec_replicas = json_data["spec"]["replicas"]
        ready_replicas = status.get("readyReplicas", "KeyNotFound")
        updated_replicas = status.get("updatedReplicas", "KeyNotFound")
        if ready_replicas == spec_replicas and updated_replicas == spec_replicas:
            return True

    elif restype == "build":
        if status.get("phase") == "Complete":
            return True

    else:
        raise ValueError(
            "Checking status for resource type {} is not supported right now".format(
                restype
            )
        )


def wait_for_ready(restype, name, timeout=300, exit_on_err=False, _result_dict=None):
    """
    Wait {timeout} for resource to be complete/ready/active.

    Args:
        restype: type of resource, which can be "build", "dc", "deploymentconfig"
        name: name of resource
        timeout: time in secs to wait for resource to become ready
        exit_on_err: if resource fails to become ready, exit with error code

    Returns:
        True if ready,
        False if timed out

    '_result_dict' can be passed when running this in a threaded fashion
    to store the result of this wait as:
        _result_dict[resource_name] = True or False
    """
    timeout_time = time.time() + timeout

    key = "{}/{}".format(restype, name)

    if _result_dict is None:
        _result_dict = dict()
    _result_dict[key] = False

    log.info("Waiting up to %dsec for '%s' to complete", timeout, key)
    while True:
        log.info("Checking if '%s' is complete...", key)

        j = get_json(restype, name)

        if _check_status_for_restype(restype, j):
            _result_dict[key] = True
            log.info("'%s' is ready!", key)
            return True  # done, return True

        if time.time() > timeout_time:
            break
        time.sleep(5)

    # if we get here, we timed out
    log.info("Timed out waiting for '%s' after %d sec", key, timeout)
    if exit_on_err:
        sys.exit(1)
    return False


def wait_for_ready_threaded(restype_name_list, timeout=300, exit_on_err=False):
    """
    Wait for multiple delpoyments in a threaded fashion.

    Args:
        restype_name_list: list of tuples with (resource_type, resource_name,)
        timeout: timeout for each thread
        exit_on_err: when all threads finish, if any failed, exit

    Returns:
        True if all deployments are ready
        False if any failed
    """
    result_dict = dict()
    threads = [
        threading.Thread(
            target=wait_for_ready, args=(restype, name, timeout, False, result_dict)
        )
        for restype, name in restype_name_list
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    failed = [key for key, result in result_dict.items() if not result]

    if failed:
        log.info("Some resources failed to become ready: %s", ", ".join(failed))
        if exit_on_err:
            sys.exit(1)
        return False
    return True
