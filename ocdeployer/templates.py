"""
Helper methods for dealing with openshift template
"""
import os
import json
import logging
import re
import yaml

from cached_property import cached_property
from jinja2 import Template as Jinja2Template

from .utils import oc, parse_restype, get_cfg_files_in_dir


log = logging.getLogger(__name__)


PARAM_REGEX = re.compile(r"(^.*)(\$\{\{\S+\}\})(.*$)", re.MULTILINE)
RESOURCE_REGEX = re.compile(r"(\d+)(\.\d+)?([A-Za-z]+)?")


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
    match = RESOURCE_REGEX.match(str(val))
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
        log.info("Adjusted limits for '%s', old: %s, new: %s", limit_key, old_val, new_val)
    for request_key, request_val in data.get("requests", {}).items():
        old_val = request_val[0] if isinstance(request_val, tuple) else request_val
        new_val = _scale_val(old_val, scale_factor)
        data["requests"][request_key] = new_val
        log.info("Adjusted requests for '%s', old: %s, new: %s", request_key, old_val, new_val)


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
        self.processed_content = {}
        self.processed_jinja_content = {}

    @staticmethod
    def _jinja_safe(data):
        """
        Mark certain sections of text as "raw" for jinja processing

        Example:
        ${{PARAM}} is valid Open Shift template syntax, but by default jinja2
        would evalulate this as a variable and replace this to "$"
        """
        return PARAM_REGEX.sub(r"\1{% raw %}\2{% endraw %}\3", data)

    @cached_property
    def content(self):
        with open(self.path, "r") as f:
            return self._jinja_safe(f.read())

    @staticmethod
    def _format_oc_parameter(param_value):
        """
        Ensures that non-string parameter values are inserted into template properly
        """
        if isinstance(param_value, bool) or isinstance(param_value, int):
            return json.dumps(param_value)
        return param_value

    def _process_via_oc(self, content, parameters=None, label=None):
        """
        Run 'oc process' on the template and update content with the processed output

        Only passes in a parameter to 'oc process' if it is listed in the template's
        "parameters" config section.

        Args:
            parameters -- dict with key param name/param value for this template
            resources_scale_factor (float) -- scale any defined resource requests/limits by
                 this number (i.e., multiple their current values by this number)
            label (str) -- label to apply to all resources
        """
        if not parameters:
            parameters = {}

        params_and_vals = {}
        for param_name, param_value in parameters.items():
            params_and_vals[param_name] = "{}={}".format(
                param_name, self._format_oc_parameter(param_value)
            )

        extra_args = []
        # Only insert the parameter if it was defined in the template
        param_names_defined_in_template = [
            param.get("name") for param in content.get("parameters", [])
        ]
        skipped_params = []
        params_and_vals_used = []
        for param_name, string in params_and_vals.items():
            if param_name in param_names_defined_in_template:
                extra_args.extend(["-p", string])
                params_and_vals_used.append(string)
            else:
                skipped_params.append(param_name)

        log.info(
            "Running 'oc process' on template '%s' with parameters '%s'",
            self.file_name,
            ", ".join([string for string in params_and_vals_used]),
        )

        if skipped_params:
            log.info(
                "Skipped parameters defined in config but not present in template: %s",
                ", ".join(skipped_params),
            )

        if label:
            extra_args.extend(["-l", label])

        output = oc(
            "process",
            "--local",
            "-f",
            "-",
            "-o",
            "json",
            *extra_args,
            _silent=True,
            _in=json.dumps(content)
        )

        return json.loads(str(output))

    def _load_content(self, string):
        if self.path.endswith(".yml") or self.path.endswith(".yaml"):
            content = yaml.safe_load(string)
        else:
            content = json.loads(string)

        # Some checks to be (semi-)sure this is a valid template...
        if content.get("kind", "").lower() != "template":
            raise ValueError("Path '{}' is not of kind 'template'".format(self.path))
        if "objects" not in content:
            raise ValueError("Path '{}' has no 'objects'".format(self.path))

        return content

    def _process_via_jinja2(self, variables):
        log.info("Rendering template '%s' with jinja2", self.file_name)
        template = Jinja2Template(self.content)
        rendered_txt = template.render(**variables)
        if not rendered_txt.strip():
            log.info("Template '%s' is empty after jinja2 processing", self.file_name)
            self.processed_jinja_content = {}
        else:
            self.processed_jinja_content = self._load_content(rendered_txt)
        return self.processed_jinja_content

    def process_jinja(self, variables):
        return self._process_via_jinja2(variables)

    def process(self, variables, resources_scale_factor=1.0, label=None):
        # Run the template through jinja processing first
        jinjafied_content = self._process_via_jinja2(variables)

        # Once that is done, run it through standard openshift template processing
        if jinjafied_content:
            self.processed_content = self._process_via_oc(
                jinjafied_content, variables.get("parameters"), label
            )
            # Scale resources in the template
            if resources_scale_factor > 0 and resources_scale_factor != 1:
                log.info(
                    "Scaling resources for template '%s' by factor of %f",
                    self.file_name,
                    resources_scale_factor,
                )
                scale_resources(self.processed_content, resources_scale_factor)
        else:
            self.processed_content = {}

        return self.processed_content

    def dump_processed_json(self):
        return json.dumps(self.processed_content)

    def get_processed_items_for_restype(self, restype):
        """
        Return list of objects of type 'restype' in the processed template

        Note at the moment this only searches the 1st level of objects (for example,
        it will not return the name of a pod embedded within a deployment config)
        """
        restype = parse_restype(restype)

        return [
            obj for obj in self.processed_content.get("items", []) if obj["kind"].lower() == restype
        ]

    def get_processed_names_for_restype(self, restype):
        """
        Return list of names for all objects of type 'restype' in the processed template

        Note at the moment this only searches the 1st level of objects (for example,
        it will not return the name of a pod embedded within a deployment config)
        """
        return [obj["metadata"]["name"] for obj in self.get_processed_items_for_restype(restype)]

    def get_processed_item(self, restype, name):
        for obj in self.get_processed_items_for_restype(restype):
            if obj["metadata"]["name"] == name:
                return obj
