import pytest
import os

from ocdeployer.secrets import SecretImporter
from ocdeployer.deploy import DeployRunner
from ocdeployer.env import EnvConfigHandler, LegacyEnvConfigHandler


def patched_runner(env_values, mock_load_vars_per_env, legacy=False):
    if not env_values:
        handler = None
    elif legacy:
        handler = LegacyEnvConfigHandler(env_files=env_values, env_dir_name="envTEST")
        handler.env_names = env_values
    else:
        handler = EnvConfigHandler(env_names=env_values, env_dir_name="envTEST")

    runner = DeployRunner(
        "templatesTEST", "test-project", handler, None, ["service"], None, None, []
    )
    runner.base_env_path = "base/envTEST"

    if handler:
        runner.env_config_handler._load_vars_per_env = mock_load_vars_per_env
    return runner


def build_mock_env_loader(base_env_data, service_set_env_data={}):
    def mock_load_vars_per_env(path=None):
        print(f"Mock loader received path: {path}")
        if path is None:
            return base_env_data
        if "base" in path and path.endswith("envTEST"):
            print("Loading mock base data")
            return base_env_data
        if "templates" in path and "service" in path and path.endswith("envTEST"):
            print("Loading mock service set data")
            return service_set_env_data
        return {}

    return mock_load_vars_per_env


@pytest.fixture
def patch_load_cfg(monkeypatch):
    """
    A fixture that returns a function which will patch 'utils.load_cfg_file' when called in a test

    The caller can specify the dict that should be returned when 'load_cfg_file' is called
    against these two paths:
    * "templatesTEST/_cfg.yml"
    * "templatesTEST/service/_cfg.yml"
    """

    def _func(base_cfg_data, service_cfg_data):
        def _patched_load_cfg_file(path):
            if path.endswith(os.path.join("templatesTEST", "service", "_cfg.yml")):
                return service_cfg_data
            if path.endswith(os.path.join("templatesTEST", "_cfg.yml")):
                return base_cfg_data
            else:
                raise Exception("Unknown path passed to load_cfg_file")

        monkeypatch.setattr("ocdeployer.deploy.load_cfg_file", _patched_load_cfg_file)

    yield _func


def test_cfg_no_env_given(patch_os_path, patch_load_cfg):
    runner = patched_runner(None, None)
    base_cfg_data = {
        "secrets": ["secret1"],
        "images": [{"image1:latest": "somerepo/image1:latest"}],
    }
    set_cfg_data = {"secrets": ["secret2"], "images": [{"image2:latest": "somerepo/image2:latest"}]}
    patch_load_cfg(base_cfg_data, set_cfg_data)

    # no env file defined that has '_cfg' key, there should be no changes to the cfg
    assert runner._get_base_cfg() == {
        "secrets": [{"name": "secret1", "envs": [], "link": []}],
        "images": [
            {
                "istag": "image1:latest",
                "from": "somerepo/image1:latest",
                "envs": [],
                "scheduled": True,
            }
        ],
    }

    assert runner._get_service_set_cfg("service", "templatesTEST/service") == {
        "secrets": [{"name": "secret2", "envs": [], "link": []}],
        "images": [
            {
                "istag": "image2:latest",
                "from": "somerepo/image2:latest",
                "envs": [],
                "scheduled": True,
            }
        ],
    }


def test_cfg_no_env_cfg(patch_os_path, patch_load_cfg):
    base_cfg_data = {
        "secrets": ["secret1"],
        "images": [{"image1:latest": "somerepo/image1:latest"}],
    }
    set_cfg_data = {"secrets": ["secret2"], "images": [{"image2:latest": "somerepo/image2:latest"}]}
    mock_var_data = {"test_env": {"service": {"some_set": "stuff"}}}
    mock_set_var_data = {"test_env": {"component": {"some_var": "stuff"}}}

    patch_load_cfg(base_cfg_data, set_cfg_data)
    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data, mock_set_var_data))

    # env file had no _cfg, so we should not see any changes to the cfg
    assert runner._get_base_cfg() == {
        "secrets": [{"name": "secret1", "envs": [], "link": []}],
        "images": [
            {
                "istag": "image1:latest",
                "from": "somerepo/image1:latest",
                "envs": [],
                "scheduled": True,
            }
        ],
    }

    assert runner._get_service_set_cfg("service", "templatesTEST/service") == {
        "secrets": [{"name": "secret2", "envs": [], "link": []}],
        "images": [
            {
                "istag": "image2:latest",
                "from": "somerepo/image2:latest",
                "envs": [],
                "scheduled": True,
            }
        ],
    }


def test_cfg_base_env_cfg(patch_os_path, patch_load_cfg):
    base_cfg_data = {
        "secrets": ["secret1"],
        "images": [{"image1:latest": "somerepo/image1:latest"}],
    }
    set_cfg_data = {"secrets": ["secret2"], "images": [{"image2:latest": "somerepo/image2:latest"}]}
    mock_var_data = {
        "test_env": {
            "_cfg": {
                "secrets": ["additional-secret1"],
                "images": [{"image1:latest": "overridden-image"}],
                "extrastuff": "things",
            },
            "service": {"some_set": "stuff"},
        }
    }
    mock_set_var_data = {"test_env": {"component": {"some_var": "stuff"}}}

    patch_load_cfg(base_cfg_data, set_cfg_data)
    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data, mock_set_var_data))

    # base env file had a _cfg, we should see base env file config merged into base config
    assert runner._get_base_cfg() == {
        "secrets": [
            {"name": "additional-secret1", "envs": [], "link": []},
            {"name": "secret1", "envs": [], "link": []},
        ],
        "extrastuff": "things",
        "images": [
            {"istag": "image1:latest", "from": "overridden-image", "envs": [], "scheduled": True}
        ],
    }

    # service set env file had no _cfg, so it should be unchanged
    assert runner._get_service_set_cfg("service", "templatesTEST/service") == {
        "secrets": [{"name": "secret2", "envs": [], "link": []}],
        "images": [
            {
                "istag": "image2:latest",
                "from": "somerepo/image2:latest",
                "envs": [],
                "scheduled": True,
            }
        ],
    }


def test_cfg_set_env_cfg(patch_os_path, patch_load_cfg):
    base_cfg_data = {
        "secrets": ["secret1"],
        "images": [{"image1:latest": "somerepo/image1:latest"}],
    }
    set_cfg_data = {"secrets": ["secret2"], "images": [{"image2:latest": "somerepo/image2:latest"}]}
    mock_var_data = {
        "test_env": {
            "_cfg": {
                "secrets": ["additional-secret1"],
                "images": [{"image1:latest": "overridden-image"}],
                "extrastuff": "things",
            },
            "service": {"some_set": "stuff"},
        }
    }
    mock_set_var_data = {
        "test_env": {
            "_cfg": {
                "secrets": ["some-secret-for-set-only"],
                "images": [
                    {"image3": "somerepo/image3:latest"},
                    {"image2:latest": "overridden-image-2"},
                ],
            },
            "component": {"some_var": "stuff"},
        }
    }

    patch_load_cfg(base_cfg_data, set_cfg_data)
    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data, mock_set_var_data))

    # base env file had a _cfg, we should see base env file config merged into base config
    assert runner._get_base_cfg() == {
        "secrets": [
            {"name": "additional-secret1", "envs": [], "link": []},
            {"name": "secret1", "envs": [], "link": []},
        ],
        "extrastuff": "things",
        "images": [
            {"istag": "image1:latest", "from": "overridden-image", "envs": [], "scheduled": True}
        ],
    }

    # set env file had a _cfg, we should see set env file config merged into set config
    assert runner._get_service_set_cfg("service", "templatesTEST/service") == {
        "secrets": [
            {"name": "some-secret-for-set-only", "envs": [], "link": []},
            {"name": "secret2", "envs": [], "link": []},
        ],
        "images": [
            {"istag": "image2:latest", "from": "overridden-image-2", "envs": [], "scheduled": True},
            {
                "istag": "image3:latest",
                "from": "somerepo/image3:latest",
                "envs": [],
                "scheduled": True,
            },
        ],
    }


def test__no_env_given():
    expected = {
        "parameters": {
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(None, None, legacy=False)
    assert runner._get_variables("service", "templates/service", "some_component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_sanity(legacy, patch_os_path):
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

    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "some_component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_merge_from_global(legacy, patch_os_path):
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

    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_service_overwrite_parameter(legacy, patch_os_path):
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

    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_service_overwrite_variable(legacy, patch_os_path):
    mock_var_data = {"test_env": {"global": {"enable_db": False}, "service": {"enable_db": True}}}

    expected = {
        "enable_db": True,
        "parameters": {
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_component_overwrite_parameter(legacy, patch_os_path):
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

    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


@pytest.mark.parametrize("legacy", (True, False), ids=("legacy=true", "legacy=false"))
def test__get_variables_component_overwrite_variable(legacy, patch_os_path):
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

    runner = patched_runner(["test_env"], build_mock_env_loader(mock_var_data), legacy)
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_base_and_service_set(patch_os_path):
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

    runner = patched_runner(
        ["test_env"], build_mock_env_loader(base_var_data, service_set_var_data)
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_service_set_only(patch_os_path):
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

    runner = patched_runner(
        ["test_env"], build_mock_env_loader(base_var_data, service_set_var_data)
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_service_set_overrides(patch_os_path):
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

    runner = patched_runner(
        ["test_env"], build_mock_env_loader(base_var_data, service_set_var_data)
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_multiple_envs(patch_os_path):
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
        build_mock_env_loader(base_var_data, service_set_var_data),
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_multiple_envs_legacy(patch_os_path):
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
        ["test_env", "test_env2", "test_env3"], build_mock_env_loader(base_var_data), legacy=True
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_multiple_envs_precedence(patch_os_path):
    base_var_data = {"test_env1": {"service/component": {"parameters": {"PARAM": "things1"}}}}

    service_set_var_data = {"test_env2": {"component": {"parameters": {"PARAM": "things2"}}}}

    expected = {
        "parameters": {
            "PARAM": "things1",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(
        ["test_env1", "test_env2"], build_mock_env_loader(base_var_data, service_set_var_data),
    )
    assert runner._get_variables("service", "templates/service", "component") == expected


def test__get_variables_multiple_envs_precedence_reversed(patch_os_path):
    base_var_data = {"test_env1": {"service/component": {"parameters": {"PARAM": "things1"}}}}

    service_set_var_data = {"test_env2": {"component": {"parameters": {"PARAM": "things2"}}}}

    expected = {
        "parameters": {
            "PARAM": "things2",
            "NAMESPACE": "test-project",
            "SECRETS_PROJECT": SecretImporter.source_project,
        },
    }

    runner = patched_runner(
        ["test_env2", "test_env1"], build_mock_env_loader(base_var_data, service_set_var_data),
    )
    assert runner._get_variables("service", "templates/service", "component") == expected
