import copy
import os
from collections import defaultdict

import appdirs
import pathlib
from cached_property import cached_property

from .utils import get_cfg_files_in_dir, load_cfg_file, object_merge


GLOBAL = "global"
APPDIRS_PATH = pathlib.Path(appdirs.user_cache_dir(appname="ocdeployer"))


def convert_to_regular_dict(data):
    if isinstance(data, defaultdict):
        data = {key: convert_to_regular_dict(val) for key, val in data.items()}
    return data


def nested_dict():
    return defaultdict(nested_dict)


class EnvConfigHandler:
    def __init__(self, env_names, env_dir_name="env"):
        path = APPDIRS_PATH / env_dir_name
        path = path if path.exists() else pathlib.Path(pathlib.os.getcwd()) / env_dir_name
        self.base_env_path = os.path.abspath(path)
        self.env_dir_name = env_dir_name
        self.env_names = env_names
        self._last_service_set = None
        self._last_merged_vars = None

    def _load_vars_per_env(self, path=None):
        data = {}

        if path:
            env_files = get_cfg_files_in_dir(path)
        else:
            env_files = get_cfg_files_in_dir(self.base_env_path)

        for file_path in env_files:
            env_name = os.path.splitext(os.path.basename(file_path))[0]
            if env_name not in self.env_names:
                continue

            data[env_name] = load_cfg_file(file_path)

        return data

    def _get_base_vars(self):
        """
        Load variables for env files located in the root 'env' dir

        Only files with a name listed in 'env_names' will be loaded.

        Returns a dict with keys/vals following this structure:
        {
            'env': {
                'service_set': {
                    'component': variables
                }
            }
        }

        "global" is a reserved service set name and component name
        """
        vars_per_env = self._load_vars_per_env()

        data = nested_dict()

        for env_name, env_vars in vars_per_env.items():
            for key, config in env_vars.items():
                if "/" in key:
                    service_set = key.split("/")[0]
                    component = key.split("/")[1]
                    data[env_name][service_set][component] = config
                else:
                    # If a specific component is not given, this is a global var
                    service_set = key
                    if service_set == GLOBAL:
                        # Global for all service sets
                        data[env_name][GLOBAL] = config
                    else:
                        # Global only for service set
                        data[env_name][service_set][GLOBAL] = config

        return convert_to_regular_dict(data)

    @cached_property
    def _base_vars(self):
        """
        Loads the base vars as a cached property, since they only need to be loaded once.
        """
        return self._get_base_vars()

    def _merge_environments(self, data):
        """
        Merge vars from multiple environments together

        Returns a dict with keys/vals following this structure:
        {
            'service_set': {
                'component': variables
            }
        }

        "global" is a reserved service set name and component name
        """
        merged_data = {}
        for _, env_data in data.items():
            if not merged_data:
                merged_data = env_data
            else:
                object_merge(env_data, merged_data)

        return merged_data

    def _merge_service_set_vars(self, service_set_dir, service_set):
        """
        Combine the env vars defined in a service set's env dir with the base env vars

        Returns a dict with keys/vals following this structure:
        {
            'env': {
                'service_set': {
                    'component': variables
                }
            }
        }

        "global" is a reserved service set name and component name
        """
        path = os.path.join(service_set_dir, self.env_dir_name)

        vars_per_env = self._load_vars_per_env(path)

        data = nested_dict()

        for env_name, env_vars in vars_per_env.items():
            for component, variables in env_vars.items():
                if "/" in component:
                    # Service-set level env files should only be defining component sections, not
                    # "service_set/component" sections ... if we find a slash then strip out
                    # the leading service set name
                    component = component.split("/")[1]
                data[env_name][service_set][component] = variables

        data = convert_to_regular_dict(data)
        merged_vars = object_merge(copy.deepcopy(self._base_vars), data)
        self._last_service_set = service_set
        self._last_merged_vars = merged_vars
        return merged_vars

    def get_vars_for_component(self, service_set_dir, service_set, component):
        """
        Handles parsing of the variables data

        The base variables file is set up in the following way:

        global:
            VAR1: "blah"
            VAR2: "blah"

        advisor:
            VAR2: "this overrides global VAR2 for only components in the advisor set"

        advisor/advisor-db:
            VAR2: "this overrides global VAR2, and advisor VAR2, for only the advisor-db component"


        The service set variables file can be set up in the following way:
        global:
            VAR2: "this overrides VAR2 for all components in service set"

        advisor-db:
            VAR2: "this overrides VAR2 for only the advisor-db component in the service set"


        The base env vars file is merged with the service set level env vars file.

        Returns:
            dict of variables/values to apply to this specific component
        """
        if service_set == self._last_service_set:
            merged_vars = self._last_merged_vars
        else:
            merged_vars = self._merge_service_set_vars(service_set_dir, service_set)

        # Combine data from multiple env files (if provided) together
        merged_vars = self._merge_environments(merged_vars)

        component_level_vars = merged_vars.get(service_set, {}).get(component, {})
        service_set_level_vars = merged_vars.get(service_set, {}).get(GLOBAL, {})
        global_vars = merged_vars.get(GLOBAL, {})

        variables = copy.deepcopy(component_level_vars)
        if "parameters" not in variables:
            variables["parameters"] = {}

        variables = object_merge(service_set_level_vars, variables)
        variables = object_merge(global_vars, variables)

        return variables


class LegacyEnvConfigHandler(EnvConfigHandler):
    """
    Allows use of --env in "legacy mode", i.e. pass in specific env files instead of env names.
    """
    def __init__(self, env_files):
        self.env_files = env_files
        self._last_service_set = None

    def _load_vars_per_env(self):
        data = {}

        for file_path in self.env_files:
            env_name = file_path
            data[env_name] = load_cfg_file(file_path)

        return data

    def _merge_service_set_vars(self, env_dir_path, service_set):
        return self._base_vars
