import pytest

from ocdeployer.secrets import SecretImporter
from ocdeployer.deploy import DeployRunner
from ocdeployer.env import EnvConfigHandler, LegacyEnvConfigHandler


def patched_runner(env_values, mock_load_vars_per_env, legacy=False):
    if legacy:
        handler = LegacyEnvConfigHandler(env_files=env_values)
    else:
        handler = EnvConfigHandler(env_names=env_values)

    runner = DeployRunner(None, "test-project", handler, None, None, None, None)
    runner.base_env_path = "base/env"
    runner.env_config_handler._load_vars_per_env = mock_load_vars_per_env
    return runner


def build_mock_loader(base_env_data, service_set_env_data={}):
    def mock_load_vars_per_env(path=None):
        if path is None or path == "base/env":
            return base_env_data
        if path == "templates/service/env":
            return service_set_env_data
        return {}

    return mock_load_vars_per_env


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_sanity(legacy):
    mock_var_data = {
        "test_env": {
            "service": {
                "enable_routes": False,
                "enable_db": False,
                "parameters": {"STUFF": "things"},
            }
        }
    }

    expected = {
        "enable_routes": False,
        "enable_db": False,
        "parameters": {
            "STUFF": "things",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "some_component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_merge_from_global(legacy):
    mock_var_data = {
        "test_env": {
            "global": {"global_variable": "global-value", "parameters": {"GLOBAL": "things"}},
            "service": {"service_variable": True, "parameters": {"STUFF": "service-stuff"}},
            "service/component": {
                "component_variable": "component",
                "parameters": {"COMPONENT": "component-param"},
            },
        }
    }

    expected = {
        "component_variable": "component",
        "global_variable": "global-value",
        "service_variable": True,
        "parameters": {
            "COMPONENT": "component-param",
            "GLOBAL": "things",
            "STUFF": "service-stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_service_overwrite_parameter(legacy):
    mock_var_data = {
        "test_env": {
            "global": {"parameters": {"STUFF": "things"}},
            "service": {"parameters": {"STUFF": "service-stuff"}},
        }
    }

    expected = {
        "parameters": {
            "STUFF": "service-stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_service_overwrite_variable(legacy):
    mock_var_data = {"test_env": {"global": {"enable_db": False}, "service": {"enable_db": True}}}

    expected = {
        "enable_db": True,
        "parameters": {
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_component_overwrite_parameter(legacy):
    mock_var_data = {
        "test_env": {
            "global": {"parameters": {"STUFF": "things"}},
            "service": {"parameters": {"THINGS": "service-things"}},
            "service/component": {"parameters": {"THINGS": "component-things"}},
        }
    }

    expected = {
        "parameters": {
            "STUFF": "things",
            "THINGS": "component-things",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_component_overwrite_variable(legacy):
    mock_var_data = {
        "test_env": {
            "global": {"enable_routes": False},
            "service": {"enable_db": True},
            "service/component": {"enable_db": False},
        }
    }

    expected = {
        "enable_routes": False,
        "enable_db": False,
        "parameters": {
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_base_and_service_set():
    base_var_data = {
        "test_env": {
            "global": {"global_var": "base_global", "parameters": {"GLOBAL_PARAM": "things"}}
        }
    }

    service_set_var_data = {
        "test_env": {
            "global": {"global_set_var": "set_global", "parameters": {"PARAM": "something"}},
            "component": {"component_var": "something", "parameters": {"ANOTHER_PARAM": "stuff"}},
        }
    }

    expected = {
        "global_var": "base_global",
        "global_set_var": "set_global",
        "component_var": "something",
        "parameters": {
            "GLOBAL_PARAM": "things",
            "PARAM": "something",
            "ANOTHER_PARAM": "stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(base_var_data, service_set_var_data))
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_service_set_only():
    base_var_data = {}

    service_set_var_data = {
        "test_env": {
            "global": {"global_set_var": "set_global", "parameters": {"PARAM": "something"}},
            "component": {"component_var": "something", "parameters": {"ANOTHER_PARAM": "stuff"}},
        }
    }

    expected = {
        "global_set_var": "set_global",
        "component_var": "something",
        "parameters": {
            "PARAM": "something",
            "ANOTHER_PARAM": "stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(base_var_data, service_set_var_data))
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_service_set_overrides():
    base_var_data = {
        "test_env": {
            "global": {"global_var": "base_global", "parameters": {"GLOBAL_PARAM": "things"}},
            "service": {"global_set_var": "blah", "parameters": {"PARAM": "blah"}},
            "service/component": {"component_var": "override this"},
        }
    }

    service_set_var_data = {
        "test_env": {
            "global": {"global_set_var": "set_global", "parameters": {"PARAM": "something"}},
            "component": {"component_var": "something", "parameters": {"ANOTHER_PARAM": "stuff"}},
        }
    }

    expected = {
        "global_var": "base_global",
        "global_set_var": "set_global",
        "component_var": "something",
        "parameters": {
            "GLOBAL_PARAM": "things",
            "PARAM": "something",
            "ANOTHER_PARAM": "stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(base_var_data, service_set_var_data))
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_multiple_envs():
    base_var_data = {
        "test_env": {
            "global": {"global_var": "base_global1", "parameters": {"GLOBAL_PARAM": "things1"}},
        },
        "test_env2": {
            "global": {"global_var": "base_global2"},
            "service/component": {"component_var": "comp2"},
        },
        "test_env3": {
            "global": {
                "global_var": "base_global3",
                "parameters": {"GLOBAL_PARAM": "things3", "ENV3_PARAM": "env3"},
            },
            "service/component": {"component_var": "comp3"},
        },
    }

    service_set_var_data = {
        "test_env": {"global": {"global_set_var": "set_global1"}},
        "test_env2": {
            "service/component": {
                "component_var": "comp2-set",
                "parameters": {"ENV2_PARAM": "env2"},
            }
        },
    }

    expected = {
        "global_var": "base_global1",
        "global_set_var": "set_global1",
        "component_var": "comp2-set",
        "parameters": {
            "GLOBAL_PARAM": "things1",
            "ENV3_PARAM": "env3",
            "ENV2_PARAM": "env2",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(
        ["test_env", "test_env2", "test_env3"],
        build_mock_loader(base_var_data, service_set_var_data),
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_multiple_envs_legacy():
    base_var_data = {
        "test_env": {
            "global": {"global_var": "base_global1", "parameters": {"GLOBAL_PARAM": "things1"}},
        },
        "test_env2": {
            "global": {"global_var": "base_global2"},
            "service/component": {"component_var": "comp2"},
        },
        "test_env3": {
            "global": {
                "global_var": "base_global3",
                "parameters": {"GLOBAL_PARAM": "things3", "ENV3_PARAM": "env3"},
            },
            "service/component": {"component_var": "comp3"},
        },
    }

    expected = {
        "global_var": "base_global1",
        "component_var": "comp2",
        "parameters": {
            "GLOBAL_PARAM": "things1",
            "ENV3_PARAM": "env3",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(
        ["test_env", "test_env2", "test_env3"], build_mock_loader(base_var_data), legacy=True
    )
    assert runner._get_variables("service", "templates/service", "component") == expected
