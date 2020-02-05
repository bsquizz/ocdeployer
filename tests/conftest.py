import pytest


def _is_test_path(path):
    return "TEST" in path


def _patch_exists(path):
    if _is_test_path(path):
        print(f"Overriding os.path.exists for path={path}")
        return True


def _patch_isdir(path):
    if _is_test_path(path):
        print(f"Overriding os.path.isdir for path={path}")
        return True


def _patch_isfile(path):
    if _is_test_path(path):
        print(f"Overriding os.path.isfile for path={path}")
        return True


@pytest.fixture
def patch_os_path(monkeypatch):
    monkeypatch.setattr("os.path.exists", _patch_exists)
    monkeypatch.setattr("os.path.isdir", _patch_isdir)
    monkeypatch.setattr("os.path.isfile", _patch_isfile)
