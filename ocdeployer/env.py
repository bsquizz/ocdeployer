import copy
import os
from collections import defaultdict

from cached_property import cached_property

from .utils import get_cfg_files_in_dir, load_cfg_file, object_merge


GLOBAL = "global"


def nested_dict():
    return defaultdict(nested_dict)


def default_to_regular(d):
    if isinstance(d, defaultdict):
        d = {k: default_to_regular(v) for k, v in d.items()}
    return d


class EnvConfigHandler:
    def __init__(self, env_names):
        self.env_names = env_names

    def get_base_env_config(self, env_dir_path):
        env_files = get_cfg_files_in_dir(env_dir_path)

        data = nested_dict()

        for file_path in env_files:
            env_data = load_cfg_file(file_path)
            env_name = os.path.splitext(os.path.basename(file_path))[0]

            for key, config in env_data.items():
                if '/' in key:
                    service_set = key.split('/')[0]
                    component = key.split('/')[1]
                    data[env_name][service_set][component] = config
                else:
                    service_set = key
                    data[env_name][service_set][GLOBAL] = config

        return default_to_regular(data)

    @cached_property
    def base_env_config(self):
        return self.get_base_env_config("env")

    def _merge_config(self, service_set_config):
        # Merge the service set config into the base config
        merged_data = object_merge(copy.deepcopy(self.base_env_config), service_set_config)[self.env_names[0]]

        # If multiple env's were listed, merge the configs of those together
        if len(self.env_names) > 1:
            for _, env_data in self.env_names[:1]:
                object_merge(merged_data, env_data)

        return merged_data

    def get_service_set_env_config(self, env_dir_path, service_set):
        env_files = get_cfg_files_in_dir(env_dir_path)

        data = nested_dict

        for file_path in env_files:
            env_data = load_cfg_file(file_path)
            env_name = os.path.splitext(os.path.basename(file_path))[0]

            for key, config in env_data.items():
                if '/' in key:
                    raise ValueError("Section names inside service set env config cannot contain '/'")
                data[env_name][service_set][GLOBAL] = config

        return self._merge_config(default_to_regular(data))