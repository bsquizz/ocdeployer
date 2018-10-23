# ocdeployer
A tool which wraps the OpenShift command line tools to enable repeatable automated deployment of OpenShift templates. Allows you to re-create environments based on templates more efficiently. Given a set of OpenShift templates, you can create a simple config that allows you to:

* Repeatedly deploy the same templates to different OpenShift projects
* Define the order in which they should deploy via 'stages'
* Optionally wait for all deployments to reach 'active' state before continuing on to the next stage
* Define which 'images' should be imported to the project
* Define which secrets your services rely on, and import them either from a local dir, or from another project in OpenShift
* Split the templates up into "service sets" and deploy all sets, or specific sets
* Define dependencies (for example: service set 'A' requires service set 'B')
* Create environment files, which define parameters that should be set at template processing time, so you can deploy the
same templates to different environments
* Create custom pre-deploy/deploy/post-deploy scripts in python if more granular control is neeed
* Quickly scale the resource request/limit defined in your templates.


**REQUIRES** OpenShift command line tools (the `oc` command)

You should log in to your project before deploying:
`$ oc login https://api.myopenshift --token=*************`


# Getting Started

## Installation and usage
```
$ python3 -m venv .venv
$ . .venv/bin/activate
(venv) $ pip install -r requirements.txt
(venv) $ ocdeployer -h
usage: ocdeployer [-h] [--no-confirm] [--secrets-local-dir SECRETS_LOCAL_DIR]
                  [--secrets-src-project SECRETS_SRC_PROJECT] [--all]
                  [--sets SETS] [--env-file ENV_FILE]
                  [--template-dir TEMPLATE_DIR] [--ignore-requires]
                  [--scale-resources SCALE_RESOURCES]
                  [--custom-dir CUSTOM_DIR] [--wipe] [--list-routes]
                  [--list-sets] [--output {yaml,json}] [--pick PICK]
                  [dst_project]

Deploy Tool

positional arguments:
  dst_project           Destination project to deploy to

optional arguments:
  -h, --help            show this help message and exit
  --no-confirm, -f      Do not prompt for confirmation
  --secrets-local-dir SECRETS_LOCAL_DIR
                        Import secrets from local files in a directory
                        (default ./secrets)
  --secrets-src-project SECRETS_SRC_PROJECT
                        Openshift project to import secrets from (default:
                        secrets)
  --all, -a             Deploy all service sets
  --sets SETS, -s SETS  Comma,separated,list of specific service set names to
                        deploy
  --env-file ENV_FILE, -e ENV_FILE
                        Path to parameters config file (default: None)
  --template-dir TEMPLATE_DIR, -t TEMPLATE_DIR
                        Template directory (default ./templates)
  --ignore-requires, -i
                        Ignore the 'requires' statement in config files and
                        deploy anyway
  --scale-resources SCALE_RESOURCES
                        Factor to scale configured cpu/memory resource
                        requests/limits by
  --custom-dir CUSTOM_DIR, -u CUSTOM_DIR
                        Custom deploy scripts directory (default ./custom)
  --wipe, -w            Wipe the project (delete EVERYTHING in it)
  --list-routes, -r     List the routes currently configured in the project
                        and exit
  --list-sets, -l       List service sets available to select in the template
                        dir and exit
  --output {yaml,json}, -o {yaml,json}
                        When using --list-* parameters, print output in yaml
                        or json format
  --pick PICK, -p PICK  Pick a single component from a service set and deploy
                        that. E.g. '-p myset/myvm'
```

## Details
`ocdeployer` relies on 4 pieces of information:
* A templates directory (default: `./templates`) -- this houses your OpenShift YAML/JSON templates as well as special config files (named `_cfg.yml`). You can split your templates into folders, called `service sets`, and define a `_cfg.yml` inside each of these folders which takes care of deploying that specific service set. The base `_cfg.yml` defines the deploy order for all service sets, as well as any "global" secrets/images that should be imported that all services rely on.
* A custom scripts directory (default: `./custom`). This is optional. Python scripts can be placed in here which can be run at pre-deploy/deploy/post-deploy stages for specific service sets.
* A secrets directory (default: `./secrets`). This is optional. Openshift YAML files containing a secret or list of secrets can be placed in here. Service sets which require imported secrets can use the secrets in this directory.
* An environment file. This is optional. Defines parameters that should be passed to the templates.

See the [examples](example/README.md) to get a better idea of how all of this configuration comes together.

### Template Configuration

The best way to explain how template configuration works is to describe the process for configuring a service set.

#### A guide to creating a service set
1. Create a new directory in the `templates` directory for your service set, e.g. "myservice"

2. Add your openshift YAML files for your services in this directory. The files should be openshift template files that contain all resources needed to get your service running (except for secrets, and image streams, we'll talk about that shortly...). This would commonly be things like `buildConfig`, `deploymentConfig`, `service`, `route`, etc.

3. Create a '_cfg.yml' file in your directory. The contents of this config file are explained below:
    ```yaml
    requires:
    # Here you can list other service sets that need to be deployed before this one can.
    # Deployment will fail when processing this file if we see the required service set has
    # not yet been deployed.
    - "myotherservice"

    secrets:
    # Lists which secrets these services rely on; they will be imported
    # A secret is only imported once, so if other services rely on the same secret
    # it won't be imported again.
    - "mysecret"

    # Indicates that there is a pre_deploy/post_deploy/deploy method defined for this
    # service set that should be used
    custom_deploy_logic: True

    images:
    # Lists the images these services require
    # key: the name used in the openshift config
    # value: the full image "pull uri" to pass to 'oc import-image'
    #
    # We check to make sure that an image with this name exists before importing it.
    # If it already exists, it will not be re-imported.
    cp-kafka: "confluentinc/cp-kafka"
    cp-zookeeper: "confluentinc/cp-zookeeper"
    nginx-stable-openshift: "docker.io/mhuth/nginx-stable-openshift"
    python-36-centos7: "centos/python-36-centos7"
    postgresql-95-rhel7: "registry.access.redhat.com/rhscl/postgresql-95-rhel7"

    deploy_order:
    # Lists the order in which components should be deployed
    stage0:
        # A stage deploys a set of components at a time
        # By default, at the end of each stage, we wait for all new DeploymentConfig's # to reach "active", unless you set 'wait' to False as seen below...
        # Stages are sorted by their name, and processed.
        wait: False
        components:
        - "zookeeper"
    stage1:
        # You can specify a wait timeout. By default, 300sec is used
        timeout: 600
        components:
        # Components lists the templates that should be deployed in this stage.
        # They are created in openshift in the same order they are listed.
        # A component can be a template file, or it can be a folder of more templates
        - "kafka"
        - "inventory-db"
    stage2:
        components:
        - "insights-inventory"
        - "upload-service"
    ```

4. If you set `custom_deploy_logic` to True, read 'Custom Deploy Logic' below.

5. Add your service folder name to the base `_cfg.yml` in the `templates` directory. Remember that the `deploy_order` specifies the order in which each service set is deployed. So if your service set depends upon other services being deployed first, order it appropriately!

6. Run `ocdeployer -l` and you should see your new component listed as a deployable service set.


### Custom Deploy Logic
If you set `custom_deploy_logic` to True, you should then create a custom deploy script in the `custom` dir with a name that matches your service set -- e.g. `deploy_myservice.py`. Inside this script you can define 3 methods:

```python
def pre_deploy(project_name, template_dir, variables_for_component):
def deploy(project_name, template_dir, components, variables_for_component, wait, timeout, resources_scale_factor):
def post_deploy(processed_templates, project_name, template_dir, variables_for_component):
```

By default, no pre_deploy/post_deploy is run, and the deploy logic is taken care of by the `ocdeployer.deploy.deploy_components` method. So, unless you are doing something complicated and require additional "python scripting" to handle your (pre/post)deploy logic, you don't need to worry about this step.

Many of the code in `ocdeployer.common` may be useful to you as you write custom deploy logic.


### Secrets

By default, `ocdeployer` will attempt to import secrets from the project `secrets` in OpenShift as well as by looking for secrets in the `./secrets` local directory. You can also use `--secrets-src-project` to copy secrets into your project from a different project in OpenShift, or use `--secrets-local-dir` to load secrets from Openshift config files in a directory.

Any .yaml/.json files you place in the `secrets-local-dir` will be parsed and secrets will be pulled out of them and imported.
The files can contain a single secret OR a list of resources

An example secret .yaml configuration:
```
apiVersion: v1
data:
    ssh-privatekey: <your key here>
kind: Secret
metadata:
    creationTimestamp: null
    name: my_secret
type: kubernetes.io/ssh-auth
```

#### How do I export secrets from a project to use later with `--secrets-local-dir`?

You can export all with:
```
$ oc export secrets -o yaml > /tmp/secrets/secrets.yaml
```

Or export a single secret object:
```
$ oc export secret mysecret -o yaml > /tmp/secrets/mysecret.yaml
```

To use the secrets files in your next project deploy:
```
(venv) $ oc login https://my.openshift --token=*************
(venv) $ ocdeployer -a --secrets-local-dir /tmp/secrets/ myproject
```

## Environment file
By default, the 'NAMESPACE' variable is passed to all templates which corresponds to the project name. You can also define an "environment"
file with more detailed variable information. Here is an example:

```yaml
global:
  VAR1: "applies to all components"
  VAR2: "also applies to all components"

advisor:
  VAR2: "this overrides global VAR2 for only components in the advisor set"
  VAR3: "VAR3 applies to all components within the advisor service set"

advisor/advisor-db:
  VAR2: "this overrides global VAR2, and advisor VAR2, for only the advisor-db component"
  VAR4: "VAR4 only applies to advisor-db"
  # Using keyword {prompt} will cause ocdeployer to prompt for this variable's value at runtime.
  VAR5: "{prompt}"
```

This allows you to define your variables at a global level, at a "per service-set" level, or at a "per-component within a service-set" level. You can override variables with the same name at the "more granular levels" as well. If an OpenShift template does not have a variable defined in its "parameters" section, then that variable will be skipped at processing time. This allows you to define variables at a global level,
but not necessarily have to define each variable as a parameter in every single template.

Select your environment file at runtime with the `-e` or `--env-file` command-line option, e.g.:
```
(venv) $ ocdeployer -s myset -e my_env_file.yml myproject
```

## Common usage

List the service sets available for you to deploy:
```
(venv) $ ocdeployer -l
Available service sets: ['platform', 'advisor', 'engine', 'vulnerability']
```

Example to deploy platform, engine, and import secrets from "mysecretsproject":
```
(venv) $ ocdeployer -s platform,engine --secrets-src mysecretsproject mynewproject
```

You can scale the cpu/memory requests/limits for all your resources using the `--scale-resources` flag:
```
(venv) $ ocdeployer -s platform --scale-resources 0.5 mynewproject
```
This will multiply any configured resource requests/limits in your template by the desired factor. If
you haven't configured a request/limit, it will not be scaled. If you scale by `0`, the resource
configuration will be entirely removed from all items, causing your default Kubernetes limit
ranges to kick in.


Delete everything (and that means pretty much everything, so be careful) from your project with:
```
(venv) $ ocdeployer --wipe <openshift project name>
```

## Known issues/needed improvements
* Currently the scripts have no way to alter the templates (other than via oc process). We may switch to a more thorough templating system in future.
* The scripts currently check to ensure deployments have moved to 'active' before exiting, however, they do not remediate any "hanging" or "stuck" builds/deployments. Work on that will be coming soon...
