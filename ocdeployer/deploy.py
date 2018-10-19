"""
Handles deploy logic for components
"""
import copy
import importlib
import logging
import os
import sys

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
        wait (boolean) -- wait for all deploymentConfigurations to be 'ready'
        timeout (int) -- timeout to wait for all deploymentConfigurations to be 'ready'
        resources_scale_factor (float) -- factor to scale cpu/memory resource requests/limits by

    Returns:
        The templates we used to deploy this service set, in the same dict format as
        get_templates_in_dir()
    """
    deployments_to_wait_for = []

    templates_by_name = get_templates_in_dir(template_dir)

    for comp_name in components:
        if comp_name not in templates_by_name:
            raise ValueError(
                "Component '{}' not found in template dir '{}'".format(
                    comp_name, template_dir
                )
            )

        template = templates_by_name.get(comp_name)
        template.process(
            variables_per_component.get(comp_name, {}), resources_scale_factor
        )
        log.info("Deploying component '{}'".format(comp_name))
        oc("apply", "-f", "-", "-n", project_name, _in=template.dump_processed_json())

        deployments = template.get_processed_names_for_restype("dc")
        for name in deployments:
            deployments_to_wait_for.append(("dc", name))

    # Wait on all deployments
    if wait:
        wait_for_ready_threaded(
            deployments_to_wait_for, timeout=timeout, exit_on_err=True
        )

    return templates_by_name


DEFAULT_DEPLOY_METHODS = (None, deploy_components, None)


def _handle_secrets_and_imgs(config):
    # Import the specified secrets
    for secret_name in config.get("secrets", []):
        SecretImporter.do_import(secret_name)

    # Import the specified images
    for img_name, img_src in config.get("images", {}).items():
        exists = oc("get", "is", img_name, _exit_on_err=False)
        if not exists:
            oc("import-image", img_name, "--from={}".format(img_src), "--confirm")


def _get_custom_methods(service_set, custom_dir):
    """
    Look for custom deploy module and import its methods.
    """
    try:
        sys.path.insert(0, custom_dir)
        module = importlib.import_module("deploy_{}".format(service_set))
        log.info("Custom script found for component '{}'".format(service_set))
    except ImportError:
        return DEFAULT_DEPLOY_METHODS

    pre_deploy_method = deploy_method = post_deploy_method = None

    try:
        pre_deploy_method = getattr(module, "pre_deploy")
        log.info("Custom pre_deploy() found for component '{}'".format(service_set))
    except AttributeError:
        pre_deploy_method = None

    try:
        deploy_method = getattr(module, "deploy")
        log.info("Custom deploy() method found for component '{}'".format(service_set))
    except AttributeError:
        deploy_method = deploy_components

    try:
        post_deploy_method = getattr(module, "post_deploy")
        log.info(
            "Custom post_deploy() method found for component '{}'".format(service_set)
        )
    except AttributeError:
        post_deploy_method = None

    return pre_deploy_method, deploy_method, post_deploy_method


def _get_deploy_methods(config, service_set_name, custom_dir):
    if config.get("custom_deploy_logic", False):
        pre_deploy_method, deploy_method, post_deploy_method = _get_custom_methods(
            service_set_name, custom_dir
        )
    else:
        pre_deploy_method, deploy_method, post_deploy_method = DEFAULT_DEPLOY_METHODS
    return pre_deploy_method, deploy_method, post_deploy_method


class DeployRunner(object):
    def __init__(
        self,
        template_dir,
        project_name,
        variables_data,
        ignore_requires,
        service_sets_selected,
        resources_scale_factor,
        custom_dir,
        specific_component=None,
    ):
        self.template_dir = template_dir
        self.custom_dir = custom_dir
        self.project_name = project_name
        self.variables_data = variables_data or {}
        self.ignore_requires = ignore_requires
        self.service_sets_selected = service_sets_selected
        self.resources_scale_factor = resources_scale_factor
        self._deployed_service_sets = []
        self.specific_component = specific_component

    def _get_variables(self, service_set, component):
        """
        Handles parsing of the variables file

        The variables file is set up in the following way:

        global:
            VAR1: "blah"
            VAR2: "blah"

        advisor:
            VAR2: "this overrides global VAR2 for only components in the advisor set"

        advisor/advisor-db:
            VAR2: "this overrides global VAR2, and advisor VAR2, for only the advisor-db component"

        Args:
        variables_data (dict) -- content of variables file
        relative_path -- example: "component/filename"

        Returns:
            dict of variables/values to apply to this specific component
        """
        variables = copy.deepcopy(self.variables_data.get("global", {}))
        variables.update({"NAMESPACE": self.project_name})
        variables.update(self.variables_data.get(service_set, {}))
        variables.update(
            self.variables_data.get("{}/{}".format(service_set, component), {})
        )
        return variables

    def _get_variables_per_component(self, service_set_content, service_set_name):
        variables_per_component = {}
        for _, stage_config in service_set_content.get("deploy_order", {}).items():
            variables_per_component.update(
                {
                    component_name: self._get_variables(
                        service_set_name, component_name
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

    def _deploy_service_set(self, service_set):
        log.info("Handling config for service set '{}'".format(service_set))
        processed_templates = {}

        dir_path = os.path.join(self.template_dir, service_set)
        cfg_path = os.path.join(dir_path, "_cfg.yml")

        if not os.path.isdir(dir_path):
            raise ValueError(
                "Unable to find directory for service set {}".format(service_set)
            )

        content = load_cfg_file(cfg_path)
        if not self.ignore_requires:
            self._check_requires(content, service_set)
        _handle_secrets_and_imgs(content)
        pre_deploy, deploy, post_deploy = _get_deploy_methods(
            content, service_set, self.custom_dir
        )
        variables_per_component = self._get_variables_per_component(
            content, service_set
        )

        deploy_order = content.get("deploy_order", {})

        if pre_deploy:
            log.info("Running pre_deploy() for service set '{}'".format(service_set))
            pre_deploy(
                project_name=self.project_name,
                template_dir=dir_path,
                variables_per_component=variables_per_component,
            )

        for stage in sorted(deploy_order.keys()):
            log.info(
                "Entering stage '{}' of config in service set '{}'".format(
                    stage, service_set
                )
            )

            # Collect the components defined in this stage
            if self.specific_component:
                # If a single component has been 'picked', just deploy that one
                components = [self.specific_component]
            else:
                components = deploy_order[stage].get("components", [])

            # Make sure all the component names have a template
            templates_found = get_templates_in_dir(dir_path)
            for comp in components:
                if comp not in templates_found:
                    raise ValueError(
                        "File for component named '{}' does not exist in service set '{}'".format(
                            comp, service_set
                        )
                    )

            log.info("Running deploy() for service set '{}'".format(service_set))

            processed_templates_this_stage = deploy(
                project_name=self.project_name,
                template_dir=dir_path,
                components=components,
                variables_per_component=variables_per_component,
                wait=deploy_order[stage].get("wait", True) is True,
                timeout=deploy_order[stage].get("timeout", 300),
                resources_scale_factor=self.resources_scale_factor,
            )
            processed_templates.update(processed_templates_this_stage)

        if post_deploy:
            log.info("Running post_deploy() for service set '{}'".format(service_set))
            post_deploy(
                processed_templates=processed_templates,
                project_name=self.project_name,
                template_dir=dir_path,
                variables_per_component=variables_per_component,
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
        _handle_secrets_and_imgs(content)
        deploy_order = content.get("deploy_order", {})

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
        for stage in sorted(deploy_order.keys()):
            service_sets = deploy_order[stage].get("components", [])
            for service_set in service_sets:
                if (
                    self.service_sets_selected
                    and service_set not in self.service_sets_selected
                ):
                    log.info(
                        "Skipping service set '{}', not selected for deploy at runtime".format(
                            service_set
                        )
                    )
                    continue
                else:
                    self._deploy_service_set(service_set)
