from __future__ import print_function

import glob
import json
import logging
import sys
import threading
import time
import os
import yaml
import re
from functools import reduce

from anytree import Node, RenderTree, PreOrderIter
import sh
from sh import ErrorReturnCode, TimeoutException
from wait_for import wait_for, TimedOutError

log = logging.getLogger(__name__)

# Resource types and their cli shortcuts
# Mostly listed here: https://docs.openshift.com/online/cli_reference/basic_cli_operations.html
SHORTCUTS = {
    "build": None,
    "buildconfig": "bc",
    "daemonset": "ds",
    "deployment": "deploy",
    "deploymentconfig": "dc",
    "event": "ev",
    "imagestream": "is",
    "imagestreamtag": "istag",
    "imagestreamimage": "isimage",
    "job": None,
    "limitrange": "limits",
    "node": "no",
    "pod": "po",
    "resourcequota": "quota",
    "replicationcontroller": "rc",
    "secrets": "secret",
    "service": "svc",
    "serviceaccount": "sa",
    "statefulset": "sts",
    "persistentvolume": "pv",
    "persistentvolumeclaim": "pvc",
    "configmap": "cm",
    "replicaset": "rs",
    "route": None,
}


INVALID_RESOURCE_REGEX = re.compile(
    r'The (\S+) "(\S+)" is invalid: metadata.resourceVersion: Invalid value: 0x0'
)


def abort():
    log.error("Hit fatal error!  Aborting.")
    sys.exit(1)


def validate_list_of_strs(item_name, section, list_):
    bad = False

    try:
        iter(list_)
    except TypeError:
        bad = True
    else:
        if not all([isinstance(i, str) for i in list_]):
            bad = True

    if bad:
        raise ValueError(f"'{item_name}' in '{section}' is not a list of strings")


def object_merge(old, new, merge_lists=True):
    """
    Recursively merge two data structures

    Thanks rsnyman :)
    https://github.com/rochacbruno/dynaconf/commit/458ffa6012f1de62fc4f68077f382ab420b43cfc#diff-c1b434836019ae32dc57d00dd1ae2eb9R15
    """
    if isinstance(old, list) and isinstance(new, list) and merge_lists:
        for item in old[::-1]:
            new.insert(0, item)
    if isinstance(old, dict) and isinstance(new, dict):
        for key, value in old.items():
            if key not in new:
                new[key] = value
            else:
                object_merge(value, new[key])
    return new


def traverse_keys(d, keys, default=None):
    """
    Allows you to look up a 'path' of keys in nested dicts without knowing whether each key exists
    """
    key = keys.pop(0)
    item = d.get(key, default)
    if len(keys) == 0:
        return item
    if not item:
        return default
    return traverse_keys(item, keys, default)


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


def get_dir(value, default_value, dir_type, optional=False):
    path = value or default_value
    required_dir_does_not_exist = not optional and not os.path.exists(path)
    required_dir_is_not_a_dir = not optional and not os.path.isdir(path)
    path_exists_but_not_a_dir = os.path.exists(path) and not os.path.isdir(path)
    if required_dir_does_not_exist:
        log.error("%s directory missing: %s", dir_type, path)
        abort()
    if required_dir_is_not_a_dir or path_exists_but_not_a_dir:
        log.error("%s directory invalid: %s", dir_type, path)
        abort()
    path = os.path.abspath(path)
    if os.path.exists(path):
        log.info("Found %s path: %s", dir_type, path)
    return path


def all_sets(template_dir):
    try:
        cfg_data = load_cfg_file(f"{template_dir}/_cfg.yaml")
    except ValueError:
        try:
            cfg_data = load_cfg_file(f"{template_dir}/_cfg.yml")
        except ValueError as err:
            log.error("Error: template dir '%s' invalid: %s", template_dir, str(err))
            abort()

    try:
        stages = cfg_data["deploy_order"]
    except KeyError:
        log.error("Error: template dir '%s' invalid: _cfg file has no 'deploy_order'")
        abort()

    sets = reduce(lambda acc, s: acc + s.get("components", []), stages.values(), [])

    return sets


def _only_immutable_errors(err_lines):
    return all("field is immutable after creation" in line.lower() for line in err_lines)


def _conflicts_found(err_lines):
    return any("error from server (conflict)" in line.lower() for line in err_lines)


def _get_logging_args(args, kwargs):
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

    return cmd_args, cmd_kwargs


def _exec_oc(*args, **kwargs):
    _silent = kwargs.pop("_silent", False)
    _ignore_immutable = kwargs.pop("_ignore_immutable", True)
    _retry_conflicts = kwargs.pop("_retry_conflicts", True)
    _stdout_log_prefix = kwargs.pop("_stdout_log_prefix", " |stdout| ")
    _stderr_log_prefix = kwargs.pop("_stderr_log_prefix", " |stderr| ")

    kwargs["_bg"] = True
    kwargs["_bg_exc"] = False

    err_lines = []
    out_lines = []

    def _err_line_handler(line, _, process):
        threading.current_thread().name = f"pid-{process.pid}"
        log.info("%s%s", _stderr_log_prefix, line.rstrip())
        err_lines.append(line)

    def _out_line_handler(line, _, process):
        threading.current_thread().name = f"pid-{process.pid}"
        if not _silent:
            log.info("%s%s", _stdout_log_prefix, line.rstrip())
        out_lines.append(line)

    retries = 3
    last_err = None
    for count in range(1, retries + 1):
        cmd = sh.oc(*args, **kwargs, _tee=True, _out=_out_line_handler, _err=_err_line_handler)
        if not _silent:
            cmd_args, cmd_kwargs = _get_logging_args(args, kwargs)
            log.info("running (pid %d): oc %s %s", cmd.pid, cmd_args, cmd_kwargs)
        try:
            return cmd.wait()
        except ErrorReturnCode as err:
            # Sometimes stdout/stderr is empty in the exception even though we appended
            # data in the callback. Perhaps buffers are not being flushed ... so just
            # set the out lines/err lines we captured on the Exception before re-raising it
            err.stdout = "\n".join(out_lines)
            err.stderr = "\n".join(err_lines)
            last_err = err

            # Ignore warnings that are printed to stderr in our error analysis
            err_lines = [line for line in err_lines if not line.lstrip().startswith("Warning:")]

            # Check if these are errors we should handle
            if _ignore_immutable and _only_immutable_errors(err_lines):
                log.warning("Ignoring immutable field errors")
                break
            elif _retry_conflicts and _conflicts_found(err_lines):
                log.warning(
                    "Hit resource conflict, retrying in 1 sec (attempt %d/%d)", count, retries
                )
                time.sleep(1)
                continue

            # Bail if not
            raise
    else:
        log.error("Retried %d times, giving up", retries)
        raise last_err


def oc(*args, **kwargs):
    """
    Run 'sh.oc' and print the command, show output, catch errors, etc.

    Optional kwargs:
        _reraise: if ErrorReturnCode is hit, don't exit, re-raise it
        _exit_on_err: sys.exit(1) if this command fails (default True)
        _silent: don't print command or resulting stdout (default False)
        _ignore_immutable: ignore errors related to immutable objects (default True)
        _retry_conflicts: retry commands if a conflict error is hit
        _stdout_log_prefix: prefix this string to stdout log output (default " |stdout| ")
        _stderr_log_prefix: prefix this string to stderr log output (default " |stderr| ")

    Returns:
        None if cmd fails and _exit_on_err is False
        command output (str) if command succeeds
    """
    _exit_on_err = kwargs.pop("_exit_on_err", True)
    _reraise = kwargs.pop("_reraise", False)
    # The _silent/_ignore_immutable/_retry_conflicts kwargs are passed on so don't pop them yet

    try:
        return _exec_oc(*args, **kwargs)
    except ErrorReturnCode:
        if _reraise:
            raise
        elif _exit_on_err:
            abort()
        else:
            if not kwargs.get("_silent"):
                log.warning("Non-zero return code ignored")


def apply_template(project, template):
    try:
        oc("apply", "-f", "-", "-n", project, _in=template.dump_processed_json(), _reraise=True)
    except ErrorReturnCode as err:
        # Work-around for resourceVersion errors.
        # See https://www.timcosta.io/kubernetes-service-invalid-clusterip-or-resourceversion/
        matches = INVALID_RESOURCE_REGEX.findall(err.stderr)
        if matches:
            for restype, name in matches:
                restype = restype.rstrip("s")  # remove plural language
                if template.get_processed_item(restype, name):  # ensure we sent this item's config
                    log.warning(
                        "Removing last-applied-configuration annotation from %s/%s", restype, name
                    )
                    oc(
                        "annotate",
                        restype,
                        name,
                        "kubectl.kubernetes.io/last-applied-configuration-",
                    )
            oc("apply", "-f", "-", "-n", project, _in=template.dump_processed_json())
        else:
            abort()


def switch_to_project(project):
    try:
        oc("get", "project", project, _reraise=True)
    except ErrorReturnCode:
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

    if restype == "deploymentconfig" or restype == "deployment":
        spec_replicas = json_data["spec"]["replicas"]
        available_replicas = status.get("availableReplicas", 0)
        updated_replicas = status.get("updatedReplicas", 0)
        unavailable_replicas = status.get("unavailableReplicas", 1)
        if unavailable_replicas == 0:
            if available_replicas == spec_replicas and updated_replicas == spec_replicas:
                return True

    elif restype == "statefulset":
        spec_replicas = json_data["spec"]["replicas"]
        ready_replicas = status.get("readyReplicas", 0)
        return ready_replicas == spec_replicas

    elif restype == "daemonset":
        desired = status.get("desiredNumberScheduled", 1)
        available = status.get("numberAvailable")
        return desired == available

    elif restype == "pod":
        if status.get("phase").lower() == "running":
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


def _wait_with_periodic_status_check(timeout, key, restype, name):
    """Check if resource is ready using _check_status_for_restype, periodically log an update."""
    time_last_logged = time.time()
    time_remaining = timeout

    def _ready():
        nonlocal time_last_logged, time_remaining

        j = get_json(restype, name)
        if _check_status_for_restype(restype, j):
            return True

        if time.time() > time_last_logged + 60:
            time_remaining -= 60
            if time_remaining:
                log.info("[%s] waiting %dsec longer", key, time_remaining)
                time_last_logged = time.time()
        return False

    wait_for(
        _ready, timeout=timeout, delay=5, message="wait for '{}' to be ready".format(key),
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
    restype = parse_restype(restype)
    key = "{}/{}".format(SHORTCUTS.get(restype) or restype, name)

    if _result_dict is None:
        _result_dict = dict()
    _result_dict[key] = False

    log.info("[%s] waiting up to %dsec for resource to be ready", key, timeout)

    try:
        # Do not use rollout status for statefulset/daemonset yet until we can handle
        # https://github.com/kubernetes/kubernetes/issues/64500
        if restype in ["deployment", "deploymentconfig"]:
            # use oc rollout status for the applicable resource types
            oc(
                "rollout",
                "status",
                key,
                _reraise=True,
                _timeout=timeout,
                _stdout_log_prefix=f"[{key}] ",
                _stderr_log_prefix=f"[{key}]  ",
            )
        else:
            _wait_with_periodic_status_check(timeout, key, restype, name)

        log.info("[%s] is ready!", key)
        _result_dict[key] = True
        return True
    except (StatusError, ErrorReturnCode) as err:
        log.error("[%s] hit error waiting for resource to be ready: %s", key, str(err))
    except (TimeoutException, TimedOutError):
        log.error("[%s] timed out waiting for resource to be ready", key)
    if exit_on_err:
        abort()
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
        thread.daemon = True
        thread.name = thread.name.lower()  # because I'm picky
        thread.start()
    for thread in threads:
        thread.join()

    failed = [key for key, result in result_dict.items() if not result]

    if failed:
        log.info("Some resources failed to become ready: %s", ", ".join(failed))
        if exit_on_err:
            abort()
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


def get_server_info():
    """Return server connected on"""
    return oc("whoami", "--show-server", _silent=True)


def cancel_builds(bc_name):
    oc(
        "cancel-build",
        f"bc/{bc_name}",
        state="new,pending,running",
        _timeout=120,
        _exit_on_err=False,
    )

    # Check if there's any lingering builds
    builds = get_json("build", label=f"openshift.io/build-config.name={bc_name}")
    lingering_builds = []
    for build in builds.get("items", []):
        # delete these builds rather than cancelling them, since jenkins pipeline builds
        # can remain stuck in certain states if OpenShift Sync plugin is broken
        status = build.get("status") or {}
        phase = status.get("phase", "").lower()
        if phase in ["new", "pending"]:
            build_name = build["metadata"]["name"]
            lingering_builds.append(build_name)

    if lingering_builds:
        log.warning("Found lingering builds for bc/%s which will be deleted", bc_name)
        for build_name in lingering_builds:
            oc("delete", "build", build_name)


def get_input_image(buildconfig, trigger):
    """
    Look up the image stream input image for a build triggered by imagechange.
    """
    bc = buildconfig
    input_image = None

    if trigger.get("imageChange", {}) != {}:
        # the image used for trigger is explicitly defined, we're done
        input_image = trigger["imageChange"]["from"]["name"]
        return input_image

    # we need to look up the image used for trigger in the bc's configuration
    # check if there's a dockerfile with FROM line
    dockerfile = traverse_keys(bc, ["spec", "source", "dockerfile"])
    if dockerfile:
        for line in dockerfile.splitlines():
            if line.startswith("FROM"):
                input_image = line.split()[1]
                if ":" not in input_image:
                    input_image = f"{input_image}:latest"
                break

    # check if the source imagestreamtag is defined in the strategy config
    for key in ["dockerStrategy", "sourceStrategy", "customStrategy"]:
        from_kind = traverse_keys(bc, ["spec", "strategy", key, "from", "kind"], "").lower()
        if from_kind == "imagestreamtag":
            input_image = bc["spec"]["strategy"][key]["from"]["name"]

    return input_image


def get_build_tree(buildconfigs):
    """
    Analyze build configurations to find which builds are 'linked'.

    Linked builds are those which output to an ImageStream that another BuildConfig then
    uses as its 'from' image.

    Returns a list of lists where item 0 in each list is the parent build and the items following
    it are all child build configs that will be fired at some point after the parent completes
    """
    bcs_using_input_image = {}
    bc_creating_output_image = {None: None}
    node_for_bc = {}
    for bc in buildconfigs:
        bc_name = bc["metadata"]["name"]
        node_for_bc[bc_name] = Node(bc_name)

        # look up output image
        if traverse_keys(bc, ["spec", "output", "to", "kind"], "").lower() == "imagestreamtag":
            output_image = bc["spec"]["output"]["to"]["name"]
            bc_creating_output_image[output_image] = bc_name

        # look up input image
        for trigger in traverse_keys(bc, ["spec", "triggers"], []):
            if trigger.get("type", "").lower() == "imagechange":
                input_image = get_input_image(bc, trigger)
                if input_image not in bcs_using_input_image:
                    bcs_using_input_image[input_image] = []
                bcs_using_input_image[input_image].append(bc_name)

    # attach each build to its parent build
    for input_image, bc_names in bcs_using_input_image.items():
        for bc_name in bc_names:
            parent_bc = bc_creating_output_image.get(input_image)
            if parent_bc:
                node_for_bc[bc_name].parent = node_for_bc[parent_bc]

    rendered_trees = []
    root_nodes = [n for _, n in node_for_bc.items() if n.is_root]
    for root_node in root_nodes:
        for pre, _, node in RenderTree(root_node):
            rendered_trees.append(f"  {pre}{node.name}")

    if rendered_trees:
        log.info("build config tree:\n\n%s", "\n".join(rendered_trees))

    return [[node.name for node in PreOrderIter(root_node)] for root_node in root_nodes]


def get_next_build(bc_name):
    """
    Return the upcoming build name we can expect to see for a given build config
    """
    json_data = get_json("bc", bc_name)
    last_version = traverse_keys(json_data, ["status", "lastVersion"], 0)
    next_build = "{}-{}".format(bc_name, last_version + 1)
    return next_build


def trigger_builds(buildconfigs):
    """
    Trigger parent build configs based on a build tree and return the resources to wait for
    """
    bcs = buildconfigs

    builds_to_wait_for = []

    build_tree = get_build_tree(bcs)
    for sub_tree in build_tree:
        parent = sub_tree[0]
        for bc_name in sub_tree:
            # Cancel any new/pending builds
            cancel_builds(bc_name)
            builds_to_wait_for.append(("build", get_next_build(bc_name)))
        log.info("triggering build for '%s'", parent)
        oc("start-build", "bc/{}".format(parent))

    return builds_to_wait_for
