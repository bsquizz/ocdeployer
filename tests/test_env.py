import pytest

import ocdeployer

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
                "service": {"enable_routes": False, "enable_db": False, "parameters": {"STUFF": "things"}}
            }
        if path == "env/other_env.yml":
            return {
                "another_service": {"somekey": "somevalue"}
            }

    monkeypatch.setattr(ocdeployer.utils, "get_cfg_files_in_dir", mock_get_cfg_files_in_dir)
    monkeypatch.setattr(ocdeployer.utils, "load_cfg_file", mock_load_cfg_file)


def test__load_vars_per_env(mock_files):
    import ocdeployer.env
    ech = ocdeployer.env.EnvConfigHandler(env_names=["test_env", "other_env"])

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

    assert ech._load_vars_per_env("env") == expected


def test__load_vars_per_env_ignore_other_env(mock_files):
    import ocdeployer.env
    ech = ocdeployer.env.EnvConfigHandler(env_names=["test_env"])

    expected = {
        'test_env': {
            'service': {
                "enable_routes": False, "enable_db": False, "parameters": {"STUFF": "things"}
            }
        }
    }

    assert ech._load_vars_per_env("env") == expected


def test__load_vars_per_env_no_envs_specified(mock_files):
    import ocdeployer.env
    ech = ocdeployer.env.EnvConfigHandler(env_names=[])

    expected = {}

    assert ech._load_vars_per_env("env") == expected


def test__load_vars_per_env_no_env_files_in_dir(mock_files):
    import ocdeployer.env
    ech = ocdeployer.env.EnvConfigHandler(env_names=['test_env'])

    expected = {}

    assert ech._load_vars_per_env("empty_env_dir") == expected