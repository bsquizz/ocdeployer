"""
Handles secrets
"""
import json
import logging

from .utils import oc, get_cfg_files_in_dir, load_cfg_file


log = logging.getLogger(__name__)


def parse_secret_file(path):
    """
    Return a dict of all secrets in a file with key: secret name, val: parsed secret json/yaml

    The file can contain 1 secret, or a list of secrets
    """
    content = load_cfg_file(path)
    secrets = {}
    if content.get("kind").lower() == "list":
        items = content.get("items", [])
    else:
        items = [content]

    for item in items:
        if item.get("kind").lower() == "secret":
            try:
                secrets[item["metadata"]["name"]] = item
            except KeyError:
                raise ValueError("Secret at path '{}' has no metadata/name".format(path))

    return secrets


def import_secrets_from_dir(path):
    files = get_cfg_files_in_dir(path)
    secrets = {}
    for secret_file in files:
        secrets_in_file = parse_secret_file(secret_file)
        log.info("Loaded secrets from file '%s", secret_file)
        for secret_name in secrets_in_file:
            if secret_name in secrets:
                raise ValueError(
                    "Secret with name '{}' defined twice in secrets dir".format(secret_name)
                )
        secrets.update(secrets_in_file)
    return secrets


def import_secret_from_project(project, secret_name):
    log.info("Importing secret '%s' from project '%s'", secret_name, project)
    oc(
        oc("get", "--export", "secret", secret_name, o="json", n=project, _silent=True),
        "apply",
        "-f",
        "-",
        _silent=True,
    )


class SecretImporter(object):
    """
    A singleton which handles importing secrets.

    Keeps track of which secrets have been imported so we don't keep re-importing.
    """

    source_project = "secrets"
    local_dir = None
    local_secrets_data = None
    local_secrets_loaded = False
    imported_secret_names = []

    @classmethod
    def _import(cls, name):
        if cls.local_dir and not cls.local_secrets_loaded:
            cls.local_secrets_data = import_secrets_from_dir(cls.local_dir)

        if cls.local_secrets_data:
            for secret_name, secret_data in cls.local_secrets_data.items():
                if secret_name == name:
                    log.info("Importing secret '%s' from local storage", name)
                    oc("apply", "-f", "-", _silent=True, _in=json.dumps(secret_data))
                    cls.imported_secret_names.append(name)

        # Check if the directory import took care of it... if not, import from project...
        if name not in cls.imported_secret_names:
            log.info("Secret '%s' not yet imported, trying import from project...", name)
            import_secret_from_project(cls.source_project, name)
            cls.imported_secret_names.append(name)

    @classmethod
    def do_import(cls, name, verify=False):
        """
        Import secret to openshift project

        If local_dir is defined, this tries to import all secrets in that dir first. If the
        secret we want is still not imported, we try to import from source_project instead
        """
        if name not in cls.imported_secret_names:
            cls._import(name)

        if verify:
            exists = oc("get", "secret", name, _exit_on_err=False)
            if not exists:
                raise AssertionError("secret '{}' does not exist after import".format(name))
