"""
Helper methods for dealing with openshift template
"""
import os
import json
import logging
import re

from .utils import oc, parse_restype, get_cfg_files_in_dir, load_cfg_file


log = logging.getLogger(__name__)


def get_templates_in_dir(path):
    """
    Given a directory path, returns a dict with keys: template name, vals: Template instance

    Note that template name is the file name minus extension
    """
    templates = [Template(path=file_path) for file_path in get_cfg_files_in_dir(path)]
    template_for_name = {os.path.basename(temp.file_name): temp for temp in templates}
    return template_for_name


def _scale_val(val, scale_factor):
    """
    Parse out the number from a kubernetes resource string and scale it by scale_factor

    Numbers are rounded to 1 decimal place.

    Examples:
    * "500Mi" scaled by .1 returns "50.0Mi"
    * "2" scaled by .5 returns "1"
    * "200m" scaled by 2 returns "400m"
    """
    match = re.match(r"(\d+)(\.\d+)?([A-Za-z]+)?", val)
    if match:
        base_num, decimal, unit = match.groups()
    else:
        return val
    float_num = float("{}{}".format(base_num, decimal if decimal else ".0"))
    return "{}{}".format(str(round(float_num * scale_factor, 1)), unit if unit else "")


def _scale_limits_and_requests(data, scale_factor):
    for limit_key, limit_val in data.get("limits", {}).items():
        old_val = limit_val[0] if isinstance(limit_val, tuple) else limit_val
        new_val = _scale_val(old_val, scale_factor)
        data["limits"][limit_key] = new_val
        log.info(
            "Adjusted limits for '%s', old: %s, new: %s", limit_key, old_val, new_val
        )
    for request_key, request_val in data.get("requests", {}).items():
        old_val = request_val[0] if isinstance(request_val, tuple) else request_val
        new_val = _scale_val(old_val, scale_factor)
        data["requests"][request_key] = new_val
        log.info(
            "Adjusted requests for '%s', old: %s, new: %s",
            request_key,
            old_val,
            new_val,
        )


def scale_resources(
    config_data, scale_factor, _current_dict_path=None, _obj_name=None, _obj_kind=None
):
    """
    Iterate through a processed config looking for any resources dictionaries.

    Scale the resource limits and requests by 'scale_factor' if any are present.
    """
    if not _current_dict_path:
        _current_dict_path = "items"
        config_data = config_data["items"]

    if isinstance(config_data, list):
        # Keep iterating thru lists
        for index, list_item in enumerate(config_data):
            scale_resources(
                list_item,
                scale_factor,
                "{}[{}]".format(_current_dict_path, index),
                _obj_name,
                _obj_kind,
            )
    elif isinstance(config_data, dict):
        # Keep iterating thru dicts
        _obj_name = config_data.get("metadata", {}).get("name") or _obj_name
        _obj_kind = config_data.get("kind") or _obj_kind
        for key, data in config_data.items():
            if key == "resources":
                # If we hit a 'resources' dict, scale it.
                if scale_factor <= 0:
                    log.info(
                        "Removing resource requests/limits for %s '%s' found at %s",
                        _obj_kind,
                        _obj_name,
                        _current_dict_path,
                    )
                    try:
                        del data["limits"]
                        del data["requests"]
                    except KeyError:
                        pass
                else:
                    log.info(
                        "Scaling resources for %s '%s' found at %s",
                        _obj_kind,
                        _obj_name,
                        _current_dict_path,
                    )
                    _scale_limits_and_requests(data, scale_factor)
            if isinstance(data, list) or isinstance(data, dict):
                scale_resources(
                    data,
                    scale_factor,
                    "{}['{}']".format(_current_dict_path, key),
                    _obj_name,
                    _obj_kind,
                )


class Template(object):
    """
    Represents an openshift template.

    A template can be processed multiple times with self.process()
    Each time it is processed, self.processed_content will get stored
    with the most recent result of that process
    """

    def __init__(self, path):
        """
        Constructor

        Args:
            path (str) -- path to file
            variables -- dict with key variable name/variable value for this template
        """
        self.path = path
        self.file_name, self.file_extension = os.path.splitext(self.path)
        self.content = self._load_content()
        self.processed_content = {}

    def _load_content(self):
        """
        Load file, store content.
        """
        content = load_cfg_file(self.path)

        # Some checks to be (semi-)sure this is a valid template...
        if content.get("kind", "").lower() != "template":
            raise ValueError("Path '{}' is not of kind 'template'".format(self.path))
        if "objects" not in content:
            raise ValueError("Path '{}' has no 'objects'".format(self.path))

        return content

    def process(self, variables, resources_scale_factor=1.0):
        """
        Run 'oc process' on the template and update content with the processed output

        Only passes in a variable to 'oc process' if it is listed in the template's
        "parameters" config section.

        TODO: maybe use our own templating engine here instead of 'oc process'?

        Args:
            variables -- dict with key variable name/variable value for this template
            resources_scale_factor (float) -- scale any defined resource requests/limits by
                 this number (i.e., multiple their current values by this number)
        """
        # Create set of param strings to pass into 'oc process'
        vars_and_vals = {}
        for var_name, var_value in variables.items():
            vars_and_vals[var_name] = "{}={}".format(var_name, var_value)

        log.info(
            "Processing template '%s' with vars '%s'",
            self.file_name,
            ", ".join([string for _, string in vars_and_vals.items()]),
        )

        # Only insert the parameter if it was defined in the template
        param_args = []
        param_names = [
            param.get("name") for param in self.content.get("parameters", [])
        ]
        skipped_vars = []
        for var_name, string in vars_and_vals.items():
            if var_name in param_names:
                param_args.extend(["-p", string])
            else:
                skipped_vars.append(var_name)

        if skipped_vars:
            log.warning(
                "Skipped variables defined in config but not present in template: %s",
                ", ".join(skipped_vars),
            )

        output = oc("process", "-f", self.path, "-o", "json", *param_args, _silent=True)

        self.processed_content = json.loads(str(output))

        if resources_scale_factor > 0 and resources_scale_factor != 1:
            log.info(
                "Scaling resources for template '%s' by factor of %f",
                self.file_name,
                resources_scale_factor,
            )
            scale_resources(self.processed_content, resources_scale_factor)
        return self.processed_content

    def dump_processed_json(self):
        return json.dumps(self.processed_content)

    def get_processed_names_for_restype(self, restype):
        """
        Return list of names for all objects of type 'restype' in the processed template

        Note at the moment this only searches the 1st level of objects (for example,
        it will not return the name of a pod embedded within a deployment config)
        """
        restype = parse_restype(restype)

        names = []

        for obj in self.processed_content.get("items", []):
            if obj["kind"].lower() == restype:
                names.append(obj["metadata"]["name"])

        return names
