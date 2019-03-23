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
from wait_for import wait_for, TimedOutError

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


def object_merge(old, new):
    """
    Recursively merge two data structures

    Thanks rsnyman :)
    https://github.com/rochacbruno/dynaconf/commit/458ffa6012f1de62fc4f68077f382ab420b43cfc#diff-c1b434836019ae32dc57d00dd1ae2eb9R15
    """
    if isinstance(old, list) and isinstance(new, list):
        for item in old[::-1]:
            new.insert(0, item)
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key not in new:
                new[key] = value
            else:
                object_merge(value, new[key])


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
        _silent: don't print command or resulting stdout (default False)
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
    out_lines = []

    def _err_line_handler(line):
        log.info(" |stderr| %s", line.rstrip())
        err_lines.append(line)

    def _out_line_handler(line):
        if not _silent:
            log.info(" |stdout| %s", line.rstrip())
        out_lines.append(line)

    try:
        return sh.oc(
            *args, **kwargs, _tee=True, _out=_out_line_handler, _err=_err_line_handler
        ).wait()
    except ErrorReturnCode as err:
        immutable_errors_only = False

        # Ignore warnings that are printed to stderr
        err_lines = [line for line in err_lines if not line.lstrip().startswith("Warning:")]

        if err_lines:
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


def get_json(restype, name=None, label=None):
    """
    Run 'oc get' for a given resource type/name/label and return the json output.

    If name is None all resources of this type are returned

    If label is not provided, then "oc get" will not be filtered on label
    """
    restype = parse_restype(restype)

    args = ["get", restype]
    if name:
        args.append(name)
    if label:
        args.extend(["-l", label])
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


class StatusError(Exception):
    pass


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

    try:
        name = json_data["metadata"]["name"]
    except KeyError:
        name = "unknown"

    if not status:
        return False

    restype = parse_restype(restype)

    if restype == "deploymentconfig":
        spec_replicas = json_data["spec"]["replicas"]
        available_replicas = status.get("availableReplicas", 0)
        updated_replicas = status.get("updatedReplicas", 0)
        unavailable_replicas = status.get("unavailableReplicas", 1)
        if unavailable_replicas == 0:
            if available_replicas == spec_replicas and updated_replicas == spec_replicas:
                return True

    elif restype == "pod":
        if status.get("phase") == "Running":
            return True

    elif restype == "build":
        phase = status.get("phase").lower()
        if phase == "cancelled":
            log.warning("Build '%s' was cancelled!", name)
            return True
        elif phase in ["completed", "complete"]:
            return True
        elif phase in ["failed", "error"]:
            raise StatusError("Build '{}' failed!".format(name))

    elif restype == "buildconfig":
        try:
            last_version = json_data["status"]["lastVersion"]
        except KeyError:
            log.debug("No builds triggered yet for 'bc/%s'", name)
            return False

        build = "{}-{}".format(name, last_version)
        log.debug("checking 'bc/%s' last triggered build: %s", name, build)
        return _check_status_for_restype("build", get_json("build", build))

    else:
        raise ValueError(
            "Checking status for resource type {} is not supported right now".format(restype)
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
    key = "{}/{}".format(restype, name)

    if _result_dict is None:
        _result_dict = dict()
    _result_dict[key] = False

    log.info("Waiting up to %dsec for '%s' to complete", timeout, key)

    def _complete():
        j = get_json(restype, name)
        if _check_status_for_restype(restype, j):
            _result_dict[key] = True
            log.info("'%s' is ready!", key)
            return True
        return False

    try:
        wait_for(
            _complete,
            timeout=timeout,
            delay=5,
            message="wait for '{}' to complete".format(key),
            log_on_loop=True,
        )
        return True
    except (TimedOutError, StatusError):
        log.exception("Hit error waiting on '%s'", key)
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
        threading.Thread(target=wait_for_ready, args=(restype, name, timeout, False, result_dict))
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


def any_pods_running(dc_name):
    """
    Return true if any pods are running in the deployment config
    """
    pod_data = get_json("pod", label="deploymentconfig={}".format(dc_name))
    if not pod_data or not len(pod_data.get("items", [])):
        log.info("No pods found for dc %s", dc_name)
        return False
    for pod in pod_data["items"]:
        if _check_status_for_restype("pod", pod):
            return True
    return False


def all_pods_running(dc_name):
    """
    Return true if all pods are running in the deployment config
    """
    pod_data = get_json("pod", label="deploymentconfig={}".format(dc_name))
    if not pod_data or not len(pod_data.get("items", [])):
        log.info("No pods found for dc %s", dc_name)
        return False
    statuses = []
    for pod in pod_data["items"]:
        statuses.append(_check_status_for_restype("pod", pod))
    return len(statuses) and all(statuses)


def no_pods_running(dc_name):
    """
    Return true if there are no pods running in the deployment
    """
    return not all_pods_running(dc_name)


def stop_deployment(dc_name, timeout=180):
    """
    Pause a deployment, delete all of its replication controllers, wait for all pods to shut down
    """
    if not any_pods_running(dc_name):
        log.info("No pods running for dc '%s', nothing to stop", dc_name)
        return

    log.info("Patching deployment config for '%s' to pause rollouts", dc_name)
    try:
        oc("rollout", "pause", "dc/{}".format(dc_name), _reraise=True)
    except sh.ErrorReturnCode as err:
        if "is already paused" in str(err.stderr):
            pass

    log.info("Removing replication controllers for '%s'", dc_name)
    rc_data = get_json("rc", label="openshift.io/deployment-config.name={}".format(dc_name))
    if not rc_data or not len(rc_data.get("items", [])):
        raise Exception("Unable to find replication controllers for '{}'".format(dc_name))
    for rc in rc_data["items"]:
        rc_name = rc["metadata"]["name"]
        oc("delete", "rc", rc_name)

    log.info("Waiting for pods related to '%s' to terminate", dc_name)
    wait_for(
        no_pods_running,
        func_args=(dc_name,),
        message="wait for deployment '{}' to be terminated".format(dc_name),
        timeout=timeout,
        delay=5,
        log_on_loop=True,
    )


def dc_ready(dc_name):
    dc_json = get_json("dc", dc_name)
    return _check_status_for_restype("dc", dc_json) and all_pods_running(dc_name)


def start_deployment(dc_name, timeout=180):
    if dc_ready(dc_name):
        log.info("Deployment '%s' already deployed and running, skipping deploy for it", dc_name)
        return

    log.info("Patching deployment config for '%s' to resume rollouts", dc_name)
    try:
        oc("rollout", "resume", "dc/{}".format(dc_name), _reraise=True)
    except sh.ErrorReturnCode as err:
        if "is not paused" in str(err.stderr):
            pass

    log.info("Triggering new deploy for '%s'", dc_name)
    try:
        oc("rollout", "latest", "dc/{}".format(dc_name), _reraise=True)
    except sh.ErrorReturnCode as err:
        if "already in progress" in str(err.stderr):
            pass

    log.info("Waiting for pod related to '%s' to finish deploying", dc_name)
    wait_for(
        dc_ready,
        func_args=(dc_name,),
        message="wait for deployment '{}' to be ready".format(dc_name),
        delay=5,
        timeout=timeout,
        log_on_loop=True,
    )
