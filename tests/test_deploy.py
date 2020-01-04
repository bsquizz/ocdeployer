from ocdeployer.secrets import SecretImporter
from ocdeployer.deploy import DeployRunner


def patched_runner(env_names, mock_load_vars_per_env):
    runner = DeployRunner(
        None, "test-project", env_names, None, None, None, None
    )
    runner.env_config_handler._load_vars_per_env = mock_load_vars_per_env
    return runner


def build_mock_loader(base_env_data, service_set_env_data={}):
    def mock_load_vars_per_env(path):
        if path == "env":
            return base_env_data
        if path == "service/env":
            return service_set_env_data
        return {}

    return mock_load_vars_per_env


def test__get_variables_sanity(monkeypatch):
    mock_var_data = {
        "test_env": {
            "service": {
                "enable_routes": False, "enable_db": False, "parameters": {"STUFF": "things"}
            }
        }
    }

    expected = {
        "enable_routes": False,
        "enable_db": False,
        "parameters": {
            "STUFF": "things",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data))
    assert runner._get_variables("service", "service/env", "some_component") == expected


def test__get_variables_merge_from_global():
    mock_var_data = {
        "test_env": {
            "global": {"global_variable": "global-value", "parameters": {"GLOBAL": "things"}},
            "service": {"service_variable": True, "parameters": {"STUFF": "service-stuff"}},
            "service/component": {
                "component_variable": "component",
                "parameters": {"COMPONENT": "component-param"},
            }
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
            "SECRETS_PROJECT": SecretImporter.source_project
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_service_overwrite_parameter():
    mock_var_data = {
        "test_env": {
            "global": {"parameters": {"STUFF": "things"}},
            "service": {"parameters": {"STUFF": "service-stuff"}}
        }
    }

    expected = {
        "parameters": {
            "STUFF": "service-stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_service_overwrite_variable():
    mock_var_data = {
        "test_env": {
            "global": {"enable_db": False}, "service": {"enable_db": True}
        }
    }

    expected = {
        "enable_db": True,
        "parameters": {
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_component_overwrite_parameter():
    mock_var_data = {
        "test_env": {
            "global": {"parameters": {"STUFF": "things"}},
            "service": {"parameters": {"THINGS": "service-things"}},
            "service/component": {"parameters": {"THINGS": "component-things"}}
        }
    }

    expected = {
        "parameters": {
            "STUFF": "things",
            "THINGS": "component-things",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_component_overwrite_variable():
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
            "SECRETS_PROJECT": SecretImporter.source_project
        },
    }

    runner = patched_runner(["test_env"], build_mock_loader(mock_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_base_and_service_set():
    base_var_data = {
        "test_env": {
            "global": {"global_var": "base_global", "parameters": {"GLOBAL_PARAM": "things"}}
        }
    }

    service_set_var_data = {
        "test_env": {
            "global": {"global_set_var": "set_global", "parameters": {"PARAM": "something"}},
            "component": {"component_var": "something", "parameters": {"ANOTHER_PARAM": "stuff"}}
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
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(base_var_data, service_set_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_service_set_only():
    base_var_data = {}

    service_set_var_data = {
        "test_env": {
            "global": {"global_set_var": "set_global", "parameters": {"PARAM": "something"}},
            "component": {"component_var": "something", "parameters": {"ANOTHER_PARAM": "stuff"}}
        }
    }

    expected = {
        "global_set_var": "set_global",
        "component_var": "something",
        "parameters": {
            "PARAM": "something",
            "ANOTHER_PARAM": "stuff",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(base_var_data, service_set_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected


def test__get_variables_service_set_overrides():
    base_var_data = {
        "test_env": {
            "global": {"global_var": "base_global", "parameters": {"GLOBAL_PARAM": "things"}},
            "service": {"global_set_var": "blah", "parameters": {"PARAM": "blah"}},
            "service/component": {"component_var": "override this"}
        }
    }

    service_set_var_data = {
        "test_env": {
            "global": {"global_set_var": "set_global", "parameters": {"PARAM": "something"}},
            "component": {"component_var": "something", "parameters": {"ANOTHER_PARAM": "stuff"}}
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
            "SECRETS_PROJECT": SecretImporter.source_project
        }
    }

    runner = patched_runner(["test_env"], build_mock_loader(base_var_data, service_set_var_data))
    assert runner._get_variables("service", "service/env", "component") == expected
