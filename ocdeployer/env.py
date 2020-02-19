import copy
import logging
import os
from collections import defaultdict

from cached_property import cached_property

from .config import merge_cfgs
from .utils import get_cfg_files_in_dir, get_dir, load_cfg_file, object_merge


log = logging.getLogger("ocdeployer.env")
GLOBAL = "global"
CFG = "_cfg"


def convert_to_regular_dict(data):
    if isinstance(data, defaultdict):
        data = {key: convert_to_regular_dict(val) for key, val in data.items()}
    return data


def nested_dict():
    return defaultdict(nested_dict)


def _dedupe_preserve_order(seq):
    """De-dupe a list, but preserve order of elements.

    https://www.peterbe.com/plog/uniqifiers-benchmark
    """
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]


class EnvConfigHandler:
    def __init__(self, env_names, env_dir_name="env"):
        env_path = os.path.join(os.getcwd(), env_dir_name)
        self.base_env_path = get_dir(env_path, env_path, "environment")  # ensures path is valid dir
        self.env_dir_name = env_dir_name
        self.env_names = _dedupe_preserve_order(env_names)
        if len(env_names) != len(self.env_names):
            log.warning("Duplicate env names provided: %s", env_names)
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
                if key == CFG:
                    # This is a env-level _cfg definition
                    data[env_name][CFG] = config
                elif key == GLOBAL:
                    # Global vars for all service sets
                    data[env_name][GLOBAL] = config
                elif "/" in key:
                    service_set = key.split("/")[0]
                    component = key.split("/")[1]
                    data[env_name][service_set][component] = config
                else:
                    # A specific component was not given, this is a service set var
                    # Global only for service set
                    service_set = key
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

        The configs from the environments take precedence based on which order they were
        supplied to the command line. Environments listed first take precedence.

        Returns a dict with keys/vals following this structure:
        {
            'service_set': {
                'component': variables
            }
        }

        "global" is a reserved service set name and component name
        """
        merged_data = {}
        for env in self.env_names:
            object_merge(data.get(env, {}), merged_data)

        return merged_data

    def _get_service_set_vars(self, service_set_dir, service_set):
        """
        Load service set env data for each environment.
        """
        path = os.path.join(service_set_dir, self.env_dir_name)
        path = get_dir(path, path, "environment", optional="True")  # ensures path is valid dir

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

        return convert_to_regular_dict(data)

    def _merge_env_cfgs(self, vars_per_env, service_set=None):
        merged_cfg = {}
        for env in self.env_names:
            cfg = {}
            if service_set:
                # Look for a _cfg key under [env][service_set]['_cfg']
                for key, data in vars_per_env.get(env, {}).items():
                    if service_set and key == service_set:
                        cfg = data.get(CFG, {})
                        break
            else:
                # Look for a _cfg key under [env]['_cfg']
                cfg = vars_per_env.get(env, {}).get(CFG, {})
            merge_cfgs(cfg, merged_cfg)
        return merged_cfg

    def get_base_env_cfg(self):
        """
        Returns data defined under the '_cfg' key in the base env files.

        If _cfg is defined in multiple env files, its data is merged with precedence according to
        what order the envs were listed in.
        """
        return self._merge_env_cfgs(self._base_vars)

    def get_service_set_env_cfg(self, service_set_dir, service_set):
        """
        Returns data defined under the '_cfg' key in a service set's env files.

        If _cfg is defined in multiple env files, its data is merged with precedence according to
        what order the envs were listed in.
        """
        return self._merge_env_cfgs(
            self._get_service_set_vars(service_set_dir, service_set), service_set=service_set
        )

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
        data = self._get_service_set_vars(service_set_dir, service_set)
        merged_vars = object_merge(copy.deepcopy(self._base_vars), data)
        self._last_service_set = service_set
        self._last_merged_vars = merged_vars

        # Don't include the '_cfg' component in this data set, it's not used for this purpose.
        if CFG in merged_vars:
            del merged_vars[CFG]

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

    @staticmethod
    def _get_env_name(file_path):
        return os.path.splitext(os.path.basename(file_path))[0]

    def __init__(self, env_files, env_dir_name="env"):
        self.env_files = env_files
        _env_names = [self._get_env_name(fp) for fp in self.env_files]
        super().__init__(_env_names, env_dir_name)

    def _load_vars_per_env(self, path=None):
        data = {}

        for file_path in self.env_files:
            data[self._get_env_name(file_path)] = load_cfg_file(file_path)

        return data

    def _merge_service_set_vars(self, env_dir_path, service_set):
        return self._base_vars
