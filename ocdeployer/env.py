import copy
import os
from collections import defaultdict

from cached_property import cached_property

from .utils import get_cfg_files_in_dir, load_cfg_file, object_merge


GLOBAL = "global"


def convert_to_regular_dict(data):
    if isinstance(data, defaultdict):
        data = {key: convert_to_regular_dict(val) for key, val in data.items()}
    return data


def nested_dict():
    return defaultdict(nested_dict)


class EnvConfigHandler:
    def __init__(self, env_names):
        self.base_env_path = "env"
        self.env_names = env_names
        self._last_service_set = None
        self._last_merged_vars = None

    def _load_vars_per_env(self, env_dir_path):
        data = {}

        env_files = get_cfg_files_in_dir(env_dir_path)

        for file_path in env_files:
            env_name = os.path.splitext(os.path.basename(file_path))[0]
            if env_name not in self.env_names:
                continue

            data[env_name] = load_cfg_file(file_path)

        return data

    def _get_base_vars(self, env_dir_path):
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
        vars_per_env = self._load_vars_per_env(env_dir_path)

        data = nested_dict()

        for env_name, env_vars in vars_per_env.items():
            for key, config in env_vars.items():
                if '/' in key:
                    service_set = key.split('/')[0]
                    component = key.split('/')[1]
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
        return self._get_base_vars(self.base_env_path)

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
        merged_data = data[self.env_names[0]]

        # If multiple env's are listed, merge the configs of those together
        if len(self.env_names) > 1:
            for _, env_data in self.env_names[:1]:
                object_merge(merged_data, env_data)

        return merged_data

    def _merge_service_set_vars(self, env_dir_path, service_set):
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
        vars_per_env = self._load_vars_per_env(env_dir_path)

        data = nested_dict()

        for env_name, env_vars in vars_per_env.items():
            for component, variables in env_vars.items():
                if '/' in component:
                    # Service-set level env files should only be defining component sections, not
                    # "service_set/component" sections ... if we find a slash then strip out
                    # the leading service set name
                    component = component.split('/')[1]
                data[env_name][service_set][component] = variables

        data = convert_to_regular_dict(data)
        merged_vars = object_merge(copy.deepcopy(self._base_vars), data)
        merged_vars = self._merge_environments(merged_vars)
        self._last_service_set = service_set
        self._last_merged_vars = merged_vars
        return merged_vars

    def get_vars_for_component(self, service_set_env_dir, service_set, component):
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
        if not self.env_names:
            return {}

        if service_set == self._last_service_set:
            merged_vars = self._last_merged_vars
        else:
            merged_vars = self._merge_service_set_vars(service_set_env_dir, service_set)

        component_level_vars = merged_vars.get(service_set, {}).get(component, {})
        service_set_level_vars = merged_vars.get(service_set, {}).get(GLOBAL, {})
        global_vars = merged_vars.get(GLOBAL, {})

        variables = copy.deepcopy(component_level_vars)
        if "parameters" not in variables:
            variables["parameters"] = {}

        variables = object_merge(service_set_level_vars, variables)
        variables = object_merge(global_vars, variables)

        return variables
