"""
Handles secrets
"""
import json
import logging

from .utils import oc, get_cfg_files_in_dir, load_cfg_file, validate_list_of_strs


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
    log.info("Loading secrets from local path: %s", path)
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


def parse_config(config):
    secrets = []

    for secret in config.get("secrets", []):
        if isinstance(secret, str):
            secrets.append({"name": secret, "link": [], "envs": []})
        elif isinstance(secret, dict):
            name = secret.get("name")
            link = secret.get("link", [])
            envs = secret.get("envs", [])

            if not name:
                raise ValueError("Secret listed in _cfg.yml is missing 'name'")
            validate_list_of_strs("link", "secrets", link)
            validate_list_of_strs("envs", "secrets", envs)

            secrets.append({"name": name, "link": link, "envs": envs})
        else:
            raise ValueError("syntax of 'secrets' section in _cfg.yml is incorrect")

    return secrets


class SecretImporter(object):
    """
    A singleton which handles importing secrets.

    Keeps track of which secrets have been imported so we don't keep re-importing.
    """

    source_project = None
    local_dir = None
    local_secrets_data = None
    local_secrets_loaded = False
    handled_secret_names = []

    @staticmethod
    def _get_secret(name):
        return oc("get", "secret", name, _exit_on_err=False)

    @classmethod
    def _import(cls, name):
        if cls.local_dir and not cls.local_secrets_loaded:
            cls.local_secrets_data = import_secrets_from_dir(cls.local_dir)

        if cls.local_secrets_data:
            for secret_name, secret_data in cls.local_secrets_data.items():
                if secret_name == name:
                    log.info("Importing secret '%s' from local storage", name)
                    oc("apply", "-f", "-", _silent=True, _in=json.dumps(secret_data))
                    cls.handled_secret_names.append(name)

        # Check if the directory import took care of it... if not, import from project...
        if cls.source_project and name not in cls.handled_secret_names:
            log.info("Secret '%s' not yet imported, trying import from project...", name)
            import_secret_from_project(cls.source_project, name)
            cls.handled_secret_names.append(name)

    @classmethod
    def handle(cls, name, link=None, verify=False, **kwargs):
        """
        Import secret to openshift project and optionally link to service accounts.

        If local_dir is defined, this tries to import all secrets in that dir first. If the
        secret we want is still not imported, we try to import from source_project instead
        """
        if not cls.local_dir and not cls.source_project:
            if not cls._get_secret(name):
                raise Exception(
                    f"Required secret '{name}' is missing in namespace and secret importing has"
                    " not been enabled via --secrets-src-project or --secrets-local-dir"
                )
            cls.handled_secret_names.append(name)

        if name not in cls.handled_secret_names:
            cls._import(name)

        if link:
            for sa in link:
                oc("secrets", "link", sa, name, "--for=pull,mount")


def import_secrets(config, env_names):
    """Import the specified secrets listed in a _cfg.yml"""
    secrets = parse_config(config)
    for secret in secrets:
        if not secret["envs"] or any([e in env_names for e in secret["envs"]]):
            SecretImporter.handle(**secret)
        else:
            log.info(
                "Skipping check/import of secret '%s', not enabled for this env", secret["name"]
            )
