import pytest

import ocdeployer.env


@pytest.fixture
def mock_files(monkeypatch):
    def mock_get_cfg_files_in_dir(path):
        if path == "env":
            return ["env/test_env.yml", "env/other_env.yml"]
        if path == "empty_env_dir":
            return []

    def mock_load_cfg_file(path):
        if path == "env/test_env.yml":
            return {
                "service": {
                    "enable_routes": False, "enable_db": False, "parameters": {"STUFF": "things"}
                }
            }
        if path == "env/other_env.yml":
            return {
                "another_service": {"somekey": "somevalue"}
            }

    # To understand why we're patching at 'ocdeployer.env'...
    # Read: https://docs.python.org/3/library/unittest.mock.html#where-to-patch
    monkeypatch.setattr("ocdeployer.env.get_cfg_files_in_dir", mock_get_cfg_files_in_dir)
    monkeypatch.setattr("ocdeployer.env.load_cfg_file", mock_load_cfg_file)


def test__load_vars_per_env(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(env_names=["test_env", "other_env"])

    expected = {
        'test_env': {
            'service': {
                "enable_routes": False, "enable_db": False, "parameters": {"STUFF": "things"}
            }
        },
        'other_env': {
            'another_service': {
                'somekey': 'somevalue'
            }
        }
    }

    assert handler._load_vars_per_env("env") == expected


def test__load_vars_per_env_ignore_other_env(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(env_names=["test_env"])

    expected = {
        'test_env': {
            'service': {
                "enable_routes": False, "enable_db": False, "parameters": {"STUFF": "things"}
            }
        }
    }

    assert handler._load_vars_per_env("env") == expected


def test__load_vars_per_env_no_envs_specified(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(env_names=[])

    expected = {}

    assert handler._load_vars_per_env("env") == expected


def test__load_vars_per_env_no_env_files_in_dir(mock_files):
    handler = ocdeployer.env.EnvConfigHandler(env_names=['test_env'])

    expected = {}

    assert handler._load_vars_per_env("empty_env_dir") == expected
