"""
Handles secrets
"""

import json

from .utils import oc, get_cfg_files_in_dir, load_cfg_file


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
                raise ValueError(
                    "Secret at path '{}' has no metadata/name".format(path)
                )

    return secrets


def import_secrets_from_dir(path):
    files = get_cfg_files_in_dir(path)
    imported_names = []
    for secret_file in files:
        secrets = parse_secret_file(secret_file)
        for secret_name, secret_data in secrets.items():
            print(
                ">>> Importing secret '{}' from '{}'".format(secret_name, secret_file)
            )
            oc("apply", "-f", "-", _silent=True, _in=json.dumps(secret_data))
            imported_names.append(secret_name)
    return imported_names


def import_secret_from_project(project, secret_name):
    print(">>> Importing secret '{}' from project '{}'".format(secret_name, project))
    oc(
        oc("export", "secret", secret_name, o="json", n=project, _silent=True),
        "apply",
        "-f",
        "-",
        _silent=True,
    )


class SecretImporter(object):
    """Stores the project we import secrets from, and the method to handle importing."""

    source_project = "secrets"
    local_dir = None

    imported_secret_names = []

    @classmethod
    def do_import(cls, name, verify=False):
        """
        Import secret to openshift project

        If local_dir is defined, this tries to import all secrets in that dir first. If the
        secret we want is still not imported, we try to import from source_project instead
        """
        if name not in cls.imported_secret_names:
            if cls.local_dir:
                print(">>> Importing secrets from dir '{}'".format(cls.local_dir))
                cls.imported_secret_names.extend(import_secrets_from_dir(cls.local_dir))

            # Check if the directory import took care of it... if not, import from project...
            if name not in cls.imported_secret_names:
                print(
                    ">>> Secret {} not yet imported, trying import from project...".format(
                        name
                    )
                )
                import_secret_from_project(cls.source_project, name)
                cls.imported_secret_names.append(name)

        if verify:
            exists = oc("get", "secret", name, _exit_on_err=False)
            if not exists:
                raise AssertionError(
                    "secret '{}' does not exist after import".format(name)
                )
