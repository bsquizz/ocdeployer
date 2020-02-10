import logging

from .utils import oc, get_json, validate_list_of_strs


log = logging.getLogger("ocdeployer.images")


def _parse_istag(istag):
    """Append "latest" tag onto istag if it has no tag."""
    if ":" not in istag:
        return f"{istag}:latest"
    return istag


def _parse_new_style(images):
    """Handles new-style images config syntax."""
    parsed_images = []

    for img in images:
        if not isinstance(img, dict):
            raise ValueError("entries in 'images' must be of type 'dict'")
        if len(img.keys()) >= 2 and all([k in img for k in ["istag", "from"]]):
            # This entry is using long-style image definition, e.g.
            #   images:
            #   - istag: "image_name"
            #   - from: "quay.io/some/image_name:image_tag"
            #   - envs: ["stage", "prod"]
            istag = _parse_istag(img["istag"])
            _from = img["from"]
            envs = img.get("envs", [])
            scheduled = img.get("scheduled", True)
        elif len(img.keys()) == 1 and all(
            [k not in img for k in ["istag", "from", "envs", "scheduled"]]
        ):
            # This entry is using short-style image definition, e.g.
            #   images:
            #   - "image_name:image_tag": "quay.io/some/image_name:image_tag"
            istag, _from = list(img.items())[0]
            istag = _parse_istag(istag)
            scheduled = True
            envs = []
        else:
            raise ValueError("Unknown syntax for 'images' section of config")

        if not isinstance(istag, str) or not isinstance(_from, str):
            raise ValueError("'istag' and 'from' must be a of type 'string'")
        validate_list_of_strs("envs", "images", envs)

        parsed_images.append({"istag": istag, "from": _from, "envs": envs, "scheduled": scheduled})

    return parsed_images


def _parse_old_style(images):
    """Handles old-style images config.

    e.g.:

    images:
        istag1: "docker.io/from-uri"
        "istag2:latest": "fedora:latest"
    """
    parsed_images = []

    for istag, _from in images.items():
        if not isinstance(istag, str) or not isinstance(_from, str):
            raise ValueError("keys and values in 'images' must be a of type 'string'")
        parsed_images.append(
            {"istag": _parse_istag(istag), "from": _from, "envs": [], "scheduled": True}
        )

    return parsed_images


def parse_config(config):
    if "images" in config:
        if isinstance(config["images"], dict):
            return _parse_old_style(config["images"])
        elif isinstance(config["images"], list):
            return _parse_new_style(config["images"])
    return []


class ImageImporter:
    """
    A singleton which handles importing images

    Keeps track of which secrets have been imported so we don't keep re-importing.
    """

    imported_istags = []

    @classmethod
    def _retag_image(cls, istag, image_from, scheduled):
        oc(
            "tag", f"--scheduled={scheduled}", "--source=docker", image_from, istag,
        )

    @classmethod
    def _import_image(cls, istag, image_from, scheduled):
        oc(
            "import-image",
            istag,
            "--from={}".format(image_from),
            "--confirm",
            f"--scheduled={scheduled}",
            _reraise=True,
        )

    @classmethod
    def do_import(cls, istag, image_from, scheduled, **kwargs):
        if istag in cls.imported_istags:
            log.warning("istag '%s' already imported, skipping repeat import...", istag)

        scheduled = "True" if scheduled else "False"
        if get_json("istag", istag):
            cls._retag_image(istag, image_from, scheduled)
        else:
            cls._import_image(istag, image_from, scheduled)


def import_images(config, env_names):
    """Import the specified images listed in a _cfg.yml"""

    images = parse_config(config)
    for img_data in images:
        istag = img_data["istag"]
        image_from = img_data["from"]
        scheduled = img_data.get("scheduled", True)
        if not img_data["envs"] or any([e in env_names for e in img_data["envs"]]):
            ImageImporter.do_import(istag, image_from, scheduled)
        else:
            log.info("Skipping import of image '%s', not enabled for this env", img_data["istag"])
