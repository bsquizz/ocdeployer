import pytest

import ocdeployer.env


@pytest.fixture
def mock_files(monkeypatch, patch_os_path):
    def mock_get_cfg_files_in_dir(path):
        if path.endswith("empty_envTEST"):
            return []
        if path.endswith("envTEST"):
            return ["envTEST/test_envTEST.yml", "envTEST/other_envTEST.yml"]

    def mock_load_cfg_file(path):
        if path == "envTEST/test_envTEST.yml":
            return {
                "service": {
                    "enable_routes": False,
                    "enable_db": False,
                    "parameters": {"STUFF": "things"},
                }
            }
        if path == "envTEST/other_envTEST.yml":
            return {"another_service": {"somekey": "somevalue"}}

    # To understand why we're patching at 'ocdeployer.env'...
    # Read: https://docs.python.org/3/library/unittest.mock.html#where-to-patch
    monkeypatch.setattr("ocdeployer.env.get_cfg_files_in_dir", mock_get_cfg_files_in_dir)
    monkeypatch.setattr("ocdeployer.env.load_cfg_file", mock_load_cfg_file)


def test__load_vars_per_env(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(
        env_names=["test_envTEST", "other_envTEST"], env_dir_name="envTEST"
    )

    expected = {
        "test_envTEST": {
            "service": {
                "enable_routes": False,
                "enable_db": False,
                "parameters": {"STUFF": "things"},
            }
        },
        "other_envTEST": {"another_service": {"somekey": "somevalue"}},
    }

    assert handler._load_vars_per_env() == expected


def test__load_vars_per_env_ignore_other_env(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(env_names=["test_envTEST"], env_dir_name="envTEST")

    expected = {
        "test_envTEST": {
            "service": {
                "enable_routes": False,
                "enable_db": False,
                "parameters": {"STUFF": "things"},
            }
        }
    }

    assert handler._load_vars_per_env() == expected


def test__load_vars_per_env_no_envs_specified(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(env_names=[], env_dir_name="envTEST")

    expected = {}

    assert handler._load_vars_per_env() == expected


def test__load_vars_per_env_no_env_files_in_dir(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(
        env_names=["test_envTEST"], env_dir_name="empty_envTEST"
    )

    expected = {}

    assert handler._load_vars_per_env() == expected
