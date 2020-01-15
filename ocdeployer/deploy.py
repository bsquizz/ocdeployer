"""
Handles deploy logic for components
"""
import importlib
import json
import logging
import os
import sys
import yaml

from sh import ErrorReturnCode

from .utils import load_cfg_file, oc, wait_for_ready_threaded
from .secrets import SecretImporter
from .templates import get_templates_in_dir


log = logging.getLogger(__name__)


def deploy_components(
    project_name,
    template_dir,
    components,
    variables_per_component,
    wait=True,
    timeout=300,
    resources_scale_factor=1.0,
    label=None,
):
    """
    Deploy a collection of components in a service-set to a project

    This will:
        1. deploy templates from {template_dir} which match each component name in {components}
            these templates will be processed with {variables}
        2. if {wait} is true, waits for any newly configured DeploymentConfig's to go 'active'

    Args:
        project_name (str) -- name of openshift project
        template_dir (str) -- path to template directory
        components (tuple of tuples) -- list of component names
            component name should match the template filename (minus the .yml/.json)
        variables_per_component (dict) -- multiple key/val pairs with:
            key: component name
            val: dict of variables to pass into 'oc process' as parameters
        images (dict) -- key/val pairs of
            key: name of image stream (as it appears in 'oc get is')
            val: full name of image to import (e.g. the full 'docker pull' name)
        secrets (list of str) -- names of secrets this service set needs to have present
        wait (boolean) -- wait for all deploymentConfigurations/builds to be 'ready'
        timeout (int) -- timeout to wait for all deploymentConfigurations to be 'ready'
        resources_scale_factor (float) -- factor to scale cpu/memory resource requests/limits by
        label (str) -- Label to apply to all deployed resources

    Returns:
        The templates we used to deploy this service set, in the same dict format as
        get_templates_in_dir()
    """
    resources_to_wait_for = []
    templates_by_name = get_templates_in_dir(template_dir)
    processed_templates_by_name = {}

    for comp_name in components:
        if comp_name not in templates_by_name:
            raise ValueError(
                "Component '{}' not found in template dir '{}'".format(comp_name, template_dir)
            )

        template = templates_by_name.get(comp_name)
        template.process(variables_per_component.get(comp_name, {}), resources_scale_factor, label)

        if not template.processed_content:
            log.info("Component %s has an empty template, skipping...", comp_name)
            continue

        processed_templates_by_name[comp_name] = template

        log.info("Deploying component '%s'", comp_name)
        oc("apply", "-f", "-", "-n", project_name, _in=template.dump_processed_json())

        # Mark certain resources in this component as ones we need to wait on
        for restype in ("dc", "bc", "sts"):
            resources_to_wait_for.extend(
                [(restype, name) for name in template.get_processed_names_for_restype(restype)]
            )

        # Re-trigger any builds for deployed build configs
        bcs = template.get_processed_names_for_restype("bc")
        for name in bcs:
            log.info("Re-triggering builds for '%s'", name)
            oc("cancel-build", "bc/{}".format(name), state="pending,new,running")
            oc("start-build", "bc/{}".format(name))

    # Wait on all resources that have been marked as 'resources to wait for'
    if wait:
        wait_for_ready_threaded(resources_to_wait_for, timeout=timeout, exit_on_err=True)

    return processed_templates_by_name


def deploy_dry_run(
    project_name,
    template_dir,
    components,
    variables_per_component,
    wait=False,
    timeout=None,
    resources_scale_factor=1.0,
    label=None,
):
    """
    Similar to deploy_components, but only processes the templates.

    Does not actually push any config
    """
    templates_by_name = get_templates_in_dir(template_dir)
    processed_templates_by_name = {}

    for comp_name in components:
        if comp_name not in templates_by_name:
            raise ValueError(
                "Component '{}' not found in template dir '{}'".format(comp_name, template_dir)
            )

        template = templates_by_name.get(comp_name)
        template.process(variables_per_component.get(comp_name, {}), resources_scale_factor, label)

        if not template.processed_content:
            log.info("Component %s has an empty template, skipping...", comp_name)
            continue

        processed_templates_by_name[comp_name] = template

    return processed_templates_by_name


DEFAULT_DEPLOY_METHODS = (None, deploy_components, None)


def _handle_secrets_and_imgs(config):
    # Import the specified secrets
    for secret_name in config.get("secrets", []):
        SecretImporter.do_import(secret_name)

    # Import the specified images
    for img_name, img_src in config.get("images", {}).items():
        try:
            oc(
                "import-image",
                img_name,
                "--from={}".format(img_src),
                "--confirm",
                "--scheduled=True",
                _reraise=True,
            )
        except ErrorReturnCode as err:
            img_name_split = img_name.split(":")
            img_name = img_name_split[0]
            if len(img_name_split) < 2:
                img_tag = "latest"
            else:
                img_tag = img_name_split[1:]

            if "use the 'tag' command if you want to change the source" in str(err.stderr):
                oc("tag", "--scheduled=True", "--source=docker", img_src, f"{img_name}:{img_tag}")


def _load_module(path, service_set):
    importlib.invalidate_caches()
    spec = importlib.util.spec_from_file_location(f"deploy_{service_set}", path)

    if not spec or not os.path.exists(path):
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[f"deploy_{service_set}"] = module
    spec.loader.exec_module(module)
    log.info("Custom script found in '%s' for service set '%s'", path, service_set)

    return module


def _get_custom_methods(service_set, service_set_dir, root_custom_dir):
    """
    Look for custom deploy module and import its methods.
    """
    module = _load_module(os.path.join(service_set_dir, "custom", "deploy.py"), service_set)
    if not module:
        module = _load_module(
            os.path.join(root_custom_dir, f"deploy_{service_set}.py"), service_set
        )
    if not module:
        log.exception("Error loading custom deploy script, using default deploy methods")
        return DEFAULT_DEPLOY_METHODS

    pre_deploy_method = deploy_method = post_deploy_method = None

    try:
        pre_deploy_method = getattr(module, "pre_deploy")
        log.info("Custom pre_deploy() found for service set '%s'", service_set)
    except AttributeError:
        pre_deploy_method = None

    try:
        deploy_method = getattr(module, "deploy")
        log.info("Custom deploy() method found for service set '%s'", service_set)
    except AttributeError:
        deploy_method = deploy_components

    try:
        post_deploy_method = getattr(module, "post_deploy")
        log.info("Custom post_deploy() method found for service set '%s'", service_set)
    except AttributeError:
        post_deploy_method = None

    log.info(
        "Service set '%s' custom pre_deploy(): %s, custom deploy(): %s, custom post_deploy(): %s",
        service_set,
        bool(pre_deploy_method),
        bool(deploy_method),
        bool(post_deploy_method),
    )

    return pre_deploy_method, deploy_method, post_deploy_method


def _get_deploy_methods(config, service_set_name, service_set_dir, root_custom_dir):
    if config.get("custom_deploy_logic", False):
        pre_deploy_method, deploy_method, post_deploy_method = _get_custom_methods(
            service_set_name, service_set_dir, root_custom_dir
        )
    else:
        pre_deploy_method, deploy_method, post_deploy_method = DEFAULT_DEPLOY_METHODS
    return pre_deploy_method, deploy_method, post_deploy_method


def generate_dry_run_content(all_processed_templates, output="yaml", to_dir=None):
    """
    Write processed template content to output directory, or print to stdout if no dir given.
    """
    if to_dir:
        to_dir = os.path.abspath(to_dir)
        try:
            os.makedirs(to_dir, exist_ok=True)
            log.info("Writing processed templates to output directory: %s", to_dir)
        except OSError as exc:
            log.error("Unable to create output directory '%s': %s", to_dir, str(exc))
            return

    for service_set, processed_templates in all_processed_templates.items():
        for template_name, template_obj in processed_templates.items():
            if not template_obj.processed_content:
                log.warning("Template '%s' had no processed content", template_name)
            else:
                if output not in ["yaml", "json"]:
                    output = "yaml"
                if output == "yaml":
                    text = yaml.dump(template_obj.processed_content, default_flow_style=False)
                else:
                    text = json.dumps(template_obj.processed_content, indent=2)

                if to_dir:
                    service_set_dir = os.path.join(to_dir, service_set)
                    os.makedirs(service_set_dir, exist_ok=True)
                    file_path = os.path.join(service_set_dir, "{}.{}".format(template_name, output))
                    with open(file_path, "w") as f:
                        f.write(text)
                else:
                    print("\n# {}/{}".format(service_set, template_name))
                    print(text)


class DeployRunner(object):
    def __init__(
        self,
        template_dir,
        project_name,
        env_config_handler,
        ignore_requires,
        service_sets_selected,
        resources_scale_factor,
        root_custom_dir,
        specific_component=None,
        label=None,
        skip=None,
        dry_run=False,
        dry_run_opts=None,
    ):
        self.template_dir = template_dir
        self.root_custom_dir = root_custom_dir
        self.project_name = project_name
        self.ignore_requires = ignore_requires
        self.service_sets_selected = service_sets_selected
        self.resources_scale_factor = resources_scale_factor
        self._deployed_service_sets = []
        self.specific_component = specific_component
        self.label = label
        self.skip = skip
        self.dry_run = dry_run
        self.dry_run_opts = dry_run_opts or {}
        self.env_config_handler = env_config_handler

    def _get_variables(self, service_set_name, service_set_dir, component):
        if self.env_config_handler:
            variables = self.env_config_handler.get_vars_for_component(
                service_set_dir, service_set_name, component
            )
        else:
            variables = {}

        # ocdeployer adds the "NAMESPACE" and "SECRETS_PROJECT" parameter by default at deploy time
        if "parameters" not in variables:
            variables["parameters"] = {}
        variables["parameters"].update(
            {"NAMESPACE": self.project_name, "SECRETS_PROJECT": SecretImporter.source_project}
        )

        return variables

    def _get_variables_per_component(self, service_set_content, service_set_dir, service_set_name):
        variables_per_component = {}
        for _, stage_config in service_set_content.get("deploy_order", {}).items():
            variables_per_component.update(
                {
                    component_name: self._get_variables(
                        service_set_name, service_set_dir, component_name
                    )
                    for component_name in stage_config.get("components", [])
                }
            )
        return variables_per_component

    def _check_requires(self, service_set_content, service_set_name):
        requires = service_set_content.get("requires", [])
        for required_set in requires:
            if required_set not in self._deployed_service_sets:
                raise ValueError(
                    "Config for '{}' requires set '{}' which has not been deployed yet.".format(
                        service_set_name, required_set
                    )
                )

    def _enter_stage(
        self,
        deploy_func,
        components,
        variables_per_component,
        stage,
        deploy_order,
        service_set,
        dir_path,
    ):
        log.info("Entering stage '%s' of config in service set '%s'", stage, service_set)

        # If a component has been skipped, remove it from our component list
        if self.skip:
            for entry in self.skip:
                entry_service_set, entry_component = entry.split("/")
                if entry_service_set == service_set and entry_component in components:
                    log.info("SKIPPING deploy for component: %s", entry_component)
                    components.remove(entry_component)

        # Make sure all the component names have a template
        templates_found = get_templates_in_dir(dir_path)
        for comp in components:
            if comp not in templates_found:
                raise ValueError(
                    "File for component named '{}' does not exist in service set '{}'".format(
                        comp, service_set
                    )
                )

        log.info("Running deploy() in stage '%s' of service set '%s'", stage, service_set)

        processed_templates_this_stage = deploy_func(
            project_name=self.project_name,
            template_dir=dir_path,
            components=components,
            variables_per_component=variables_per_component,
            wait=deploy_order[stage].get("wait", True) is True,
            timeout=deploy_order[stage].get("timeout", 300),
            resources_scale_factor=self.resources_scale_factor,
            label=self.label,
        )
        return processed_templates_this_stage

    def _deploy_stage(
        self, deploy_func, variables_per_component, stage, deploy_order, service_set, dir_path
    ):
        components = deploy_order[stage].get("components", [])
        if self.specific_component:
            if self.specific_component in components:
                # If a single component has been 'picked', deploy only that one
                components = [self.specific_component]
            else:
                # If the single component is not in this stage, do not run deploy for this stage
                log.info(
                    "Skipping stage '%s', component '%s' is not part of this stage",
                    stage,
                    self.specific_component,
                )
                return {}

        return self._enter_stage(
            deploy_func,
            components,
            variables_per_component,
            stage,
            deploy_order,
            service_set,
            dir_path,
        )

    def _deploy_service_set(self, service_set):
        log.info("Handling config for service set '%s'", service_set)
        processed_templates = {}

        dir_path = os.path.join(self.template_dir, service_set)
        cfg_path = os.path.join(dir_path, "_cfg.yml")

        if not os.path.isdir(dir_path):
            raise ValueError("Unable to find directory for service set {}".format(service_set))

        content = load_cfg_file(cfg_path)
        if not self.ignore_requires:
            self._check_requires(content, service_set)

        if self.dry_run:
            log.info("Doing a DRY RUN of deployment")
            pre_deploy_func, deploy_func, post_deploy_func = None, deploy_dry_run, None
        else:
            _handle_secrets_and_imgs(content)

            pre_deploy_func, deploy_func, post_deploy_func = _get_deploy_methods(
                content, service_set, dir_path, self.root_custom_dir
            )

        variables_per_component = self._get_variables_per_component(content, dir_path, service_set)

        deploy_order = content.get("deploy_order", {})

        if pre_deploy_func:
            log.info("Running pre_deploy() for service set '%s'", service_set)
            pre_deploy_func(
                project_name=self.project_name,
                template_dir=dir_path,
                variables_per_component=variables_per_component,
            )

        for stage in sorted(deploy_order.keys()):
            processed_templates.update(
                self._deploy_stage(
                    deploy_func, variables_per_component, stage, deploy_order, service_set, dir_path
                )
            )

        if post_deploy_func:
            log.info("Running post_deploy() for service set '%s'", service_set)
            post_deploy_func(
                processed_templates=processed_templates,
                project_name=self.project_name,
                template_dir=dir_path,
                variables_per_component=variables_per_component,
                timeout=int(content.get("post_deploy_timeout", 0)),
            )

        self._deployed_service_sets.append(service_set)
        return processed_templates

    def run(self):
        """
        Load the "_cfg.yml" at {dir_path}, parse it, and deploy the defined components
        according to the defined stages.

        The base template dir can contain folders of other components (called service sets)
        which each have their own _cfg.yml

        Components from each stage are deployed and we wait for them to all reach
        a "ready" state before moving on to the next stage.

        If 'return_immediately' is set to 'True' under a stage, then we will not wait.
        """
        self._deployed_service_sets = []

        content = load_cfg_file(os.path.join(self.template_dir, "_cfg.yml"))
        deploy_order = content.get("deploy_order", {})

        if not self.dry_run:
            _handle_secrets_and_imgs(content)

        # Verify all service sets exist
        all_service_sets = []
        for stage, stage_data in deploy_order.items():
            all_service_sets.extend(stage_data.get("components", []))
        if self.service_sets_selected:
            for service_set in self.service_sets_selected:
                if service_set not in all_service_sets:
                    raise ValueError(
                        "Service set '{}' not found in base config.".format(service_set)
                    )

        # Deploy the service sets in proper order
        all_processed_templates = {}

        for stage in sorted(deploy_order.keys()):
            service_sets = deploy_order[stage].get("components", [])
            for service_set in service_sets:
                if self.service_sets_selected and service_set not in self.service_sets_selected:
                    log.info(
                        "Skipping service set '%s', not selected for deploy at runtime", service_set
                    )
                    continue
                else:
                    all_processed_templates[service_set] = self._deploy_service_set(service_set)

        if self.dry_run:
            generate_dry_run_content(all_processed_templates, **self.dry_run_opts)
