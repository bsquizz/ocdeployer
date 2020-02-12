# ocdeployer

[![Build Status](https://travis-ci.org/bsquizz/ocdeployer.svg?branch=master)](https://travis-ci.org/bsquizz/ocdeployer)

A tool which wraps the OpenShift command line tools to enable repeatable automated deployment of OpenShift templates. Allows you to re-create environments based on templates more efficiently. Given a set of OpenShift templates, you can create a simple config that allows you to:

* Repeatedly deploy the same templates to different OpenShift projects
* Define the order in which they should deploy via 'stages'
* Optionally wait for resources to be "ready" before continuing on to the next stage:
  * `DeploymentConfig` resources to reach desired replica count
  * `Build` resources to succeed
  * `StatefulSet` resources to reach desired replica count
* Define which external 'images' should be imported to the project as ImageStreams
* Define which secrets your services rely on, and import them either from a local dir, or from another project in OpenShift
* Split component templates up into "service sets" and deploy all sets, or specific sets
* Define dependencies (for example: service set 'A' requires service set 'B')
* Create environment files, which define parameters that should be set at template processing time, so you can deploy the same templates to different environments
* Specify multiple environment files at deploy time and merge them
* Use OpenShift templating along with jinja2 templating
* Create custom pre-deploy/deploy/post-deploy scripts in python if more granular control is neeed
* Quickly scale the resource request/limit defined in your templates.
* Watch events in the namespace during deploy


**REQUIRES** OpenShift command line tools (the `oc` command)

You should log in to your project before deploying:

`$ oc login https://api.myopenshift --token=*************`


# Getting Started

## Details
A example `ocdeployer` project structure looks like the following:

```
├── env
│   ├── prod-env.yml
│   └── qa-env.yml
├── secrets
│   ├── mysql-secrets.yml
│   └── postgres-secrets.yml
└── templates
    ├── _cfg.yml
    ├── set1
    │   ├── _cfg.yml
    │   ├── env
    │   │   └── qa-env.yml
    │   ├── nginx.yml
    │   └── postgres.yml
    └── set2
        ├── _cfg.yml
        ├── custom
        │   └── deploy.py
        ├── mysql.yml
        └── ruby-app.yml
```

Components of the structure are explained below:

* `secrets` directory -- This is optional. Holds Openshift YAML files containing a `Secret` or `List` of `Secret` resources. Applications which require imported secrets can use the secrets in this directory.

* `env` directory -- You can create env files at the root level of the project, as well as in each service set (discussed below). You can specify multiple environments on the CLI at deploy time and the values will be merged.

* `templates` directory -- OpenShift YAML/JSON templates as well as a special config file (named _cfg.yml). Templates are typically split into into folders in this directory, called service sets. The base _cfg.yml defines the deploy order for all service sets, as well as any "global" secrets/images that should be imported that all services rely on.
* `set1` and `set2` are service set folders -- Each service set folder contains the following:
  * A `_cfg.yml` which configures deployment of that specific service set.
  * A directory named `custom` which houses custom deploy scripts. This is optional. Python scripts can be placed in here which can be run at pre-deploy/deploy/post-deploy stages for specific service sets.
  * A directory named `env` that contains an environment file or files. This is optional. Defines parameters that should be passed to the templates. Values defined in this env file are merged with values defined at the root level, with matching keys in the service set file taking precedence.

See the [examples](example/README.md) to get a better idea of how all of this configuration comes together and to get more details on what ocdeployer does when you run a deploy.

## Installation and usage

### Installation
You can install this package from pypi using:
```
$ pip install ocdeployer
```

Or, if you're installing from source, install into a virtual environment:
```
$ python3 -m venv .venv
$ . .venv/bin/activate
(venv) $ pip install -r requirements.txt
```

### Usage
```
(venv) $ ocdeployer -h
Usage: ocdeployer [OPTIONS] COMMAND [ARGS]...

  Deploys components to a given cluster. NOTE: You need the openshift cli
  tool ('oc') installed and to login to your openshift cluster before
  running the tool.

Options:
  -h, --help  Show this message and exit.

Commands:
  deploy       Deploy to project
  list-routes  List routes currently in the project
  list-sets    List service sets available in template dir
  process      Process templates but do not deploy
  wipe         Delete everything from project
```

#### Deploy command
The `deploy` command is used to push your templates into OpenShift.

Use `ocdeployer deploy -h` for more details on parameters and options.

#### Process command

Use `process` to view the template data without actually deploying it. `process` has very similar options to `deploy`, but instead of pushing any configuration to OpenShift, it will simply parse the templates with jinja2 and OpenShift templating (i.e. running `oc process`), substitute in the given variables/project name/etc., and then either print out the resulting configuration to stdout or save the resulting processed files to a directory.

#### Wipe command

Use `wipe` to delete objects from a project. It essentially runs the following commands, deleting all objects or objects which have a specific label:

```
oc delete all [--all or --selector mylabel=myvalue]
oc delete configmap [--all or --selector mylabel=myvalue]
oc delete secret [--all or --selector mylabel=myvalue]
oc delete pvc [--all or --selector mylabel=myvalue]
```

#### List-routes command

Use `list-routes` to simply print the URLs for active routes in a project

#### List-sets command

Use `list-sets` to simply print the names of service sets that are available for deployment in your templates directory.

---
## Template Configuration

The best way to explain how template configuration works is to describe the process for configuring a service set.

#### A guide to creating a service set
1. Create a new directory in the `templates` directory for your service set, e.g. "myservice"

2. Add your OpenShift YAML files for your services in this directory. The files should be OpenShift template files that contain all resources needed to get your service running (except for secrets, and image streams of external images, we'll talk about that shortly...). This would commonly be things like `buildConfig`, `deploymentConfig`, `service`, `route`, etc.

3. Create a '_cfg.yml' file in your directory. The contents of this config file are explained below:
    ```yaml
    

    # (optional) requires
    #
    # Here you can list other service sets that need to be deployed before this one can.
    # Deployment will fail when processing this file if we see the required service set has
    # not yet been deployed in this run of ocdeployer.
    requires:
    - "myotherservice"

    # (optional) secrets
    #
    # Lists which secrets apps in this service set rely on; they will be imported at run time.
    # A secret is only imported once per deploy run, so if other service sets rely on the same
    # secret it won't be imported again.
    secrets:
    - "mysecret"
    # You can also specify which service accounts a secret should be linked to
    - name: "othersecret"
      link: ["builder"]

    # (optional) custom_deploy_logic
    #
    # Indicates that there is a pre_deploy/post_deploy/deploy method defined for this
    # service set in the 'custom' folder that should be used
    custom_deploy_logic: true

    # (optional) post_deploy_timeout
    #
    # Indicates how long custom post_deploy logic should take before timeout (in seconds).
    # A null value can be handled differently depending on the post deploy logic,
    # but it is recommended that it means there is "no waiting" that will occur
    post_deploy_timeout: 300

    # (optional) images
    # Lists the image streams these services require to be imported into the destination namespace.
    #
    # key = the name or name:tag for the image stream. If no tag is specified, 'latest' is used.
    # value = the full image docker uri
    #
    # 'oc import-image <key> --from="<value>"' is run at deploy time.
    #
    # If it already exists, it will be re-imported (and therefore the image will be updated)
    images:
    - cp-kafka: "confluentinc/cp-kafka"
    - cp-zookeeper: "confluentinc/cp-zookeeper"
    - nginx-stable-openshift: "docker.io/mhuth/nginx-stable-openshift"
    - python-36-centos7: "centos/python-36-centos7"
    - postgresql-95-rhel7: "registry.access.redhat.com/rhscl/postgresql-95-rhel7"

    # (required) deploy_order
    #
    # Lists the order in which components in this service set should be deployed.
    deploy_order:
      # A stage deploys a group of components in sequentially and then waits for them to reach 'active'
      # in parallel. By default, at the end of each stage, we wait for:
      #   * DeploymentConfig's to be "active"
      #   * StatefulSet's to be "active"
      #   * BuildConfig's to succeed
      #
      # Setting 'wait' to false under the stage disables this behavior.
      #
      # Stages are processed after being sorted by name.
      stage0:
        wait: false
        components:
        - "zookeeper"
      stage1:
        # You can specify a wait timeout. By default, 300sec is used
        timeout: 600
        # 'components' lists the template files that should be deployed in this stage.
        # Their config is applied in openshift in the same order they are listed.
        components:
        - "kafka"
        - "inventory-db"
      stage2:
        components:
        - "insights-inventory"
        - "upload-service"
    ```

4. If you set `custom_deploy_logic` to True, read 'Custom Deploy Logic' below.

5. If you wish to define env vars for this service's templates, read 'Environment Files' below.

5. Add your service folder name to the base `_cfg.yml` in the `templates` directory. Remember that the `deploy_order` specifies the order in which each service set is deployed. So if your service set depends upon other services being deployed first, order it appropriately!

6. Run `ocdeployer list-sets` and you should see your new component listed as a deployable service set.


### Custom Deploy Logic

By default, no pre_deploy/post_deploy is run, and the deploy logic is taken care of by the `ocdeployer.deploy.deploy_components` method. So, unless you are doing something complicated and require additional "python scripting" to handle your (pre/post) deploy logic, you don't need to worry about custom logic (and it's quite rare that you'd want to re-write the main deploy logic itself)

But let's say you want to perform some tasks prior to deploying your components, or after deploying your components. Custom scripts can be useful for this.

You can set `custom_deploy_logic` in your service set's `_cfg.yml` to `true`. You then have two options for defining a custom deploy script:

* You can define a single `deploy.py` in the root `custom` directory of your project. This script will apply to all service sets as long as they have `custom_deploy_logic` set to `true`.
* You can create a script called `deploy.py` in the `custom` dir of your service set. This script will apply only to that service set.

If a project contains a `deploy.py` in the root custom directory as well as in the service set's custom directory -- the service set deploy script takes precedence.

---
**NOTE**

In previous versions of `ocdeployer<4.0`, the `custom` dir was housed in the root folder of the project, and the deploy file inserted in there needed to match the name of your service set, e.g. `deploy_myservice.py`. For backward compatibility, this method of defining the service set deploy scripts is still supported.

---

 Inside this script you can define 3 methods:

```python
def pre_deploy(project_name, template_dir, variables_for_component):
```
* `project_name`: string, name of project being deployed to
* `template_dir`: string, the full path to the directory of the service set's templates which are being deployed
* `variables_for_component`: dict, keys are each component name in your service set, values are another dict consisting of the variables parsed from the `env.yml` file.

```python
def deploy(project_name, template_dir, components, variables_for_component, wait, timeout, resources_scale_factor, label):
```
* `project_name`: string, name of project being deployed to
* `template_dir`: string, the full path to the directory of the service set's templates which are being deployed
* `components`: list of strings, the component names from your service set that are being deployed
* `variables_for_component`: dict, keys are each component name in your service set, values are another dict consisting of the variables parsed from the `env.yml` file.
* `wait`: boolean, used to determine whether the deploy logic should wait for things such as DeploymentConfig and BuildConfig to "finish" (go 'active', or build with success, respectively)
* `timeout`: int, how long to wait for before timing out
* `resources_scale_factor`: float, the value passed in to --scale-resources when running ocdeployer
* `label`: string, the label attached to each object in Open Shift at deploy time

```python
def post_deploy(processed_templates, project_name, template_dir, variables_for_component):
```
* `processed_templates`: dict with containing the processed template info for each component that was deployed -- keys: template name, vals: an instance of `ocdeployer.templates.Template` 
* `project_name`: string, name of project being deployed to
* `template_dir`: string, the full path to the directory of the service set's templates which are being deployed
* `variables_for_component`: dict, keys are each component name in your service set, values are another dict consisting of the variables parsed from the `env.yml` file.

Much of the code in `ocdeployer.common` may be useful to you as you write custom deploy logic (such as the `oc` method used to run oc commands).


#### Custom Deploy Example 1
Let's say that after you deploy your components, you want to trigger a build on any build configurations you pushed (actually, this already happens after each stage by default as long as `wait` is not `false` on the stage in your service set `_cfg.yml` -- but play along for the sake of this example).

You could define a post-deploy method that looks like this:
```python
from ocdeployer.utils import oc, wait_for_ready_threaded

log = logging.getLogger(__name__)

def post_deploy(**kwargs):
    build_config_names = []
    for _, template in kwargs.get("processed_templates", {}).items():
        # Get the name of all build configs that were deployed
        # Remember, we are looking at the processed template info
        # We're looking at the template AFTER variable substitution occurred.
        build_config_names.extend(template.get_processed_names_for_restype("bc"))

    objs_to_wait_for = []
    for bc_name in build_config_names:
        oc("start-build", bc_name, exit_on_err=False)
        objs_to_wait_for.append(("bc", bc_name))
    else  
        log.warning("No build configs were deployed, nothing to do")

    # Wait for all builds to reach 'completed' state:
    if objs_to_wait_for:
        wait_for_ready_threaded(objs_to_wait_for)
```

#### Custom Deploy Example 2
Let's say that when any `ConfigMap` resources in your project update, you want to trigger a new rollout of a deployment. You could do that by tracking the state of the `ConfigMap` before deploying and comparing it to the state after.

```python
from ocdeployer.utils import oc, get_json, wait_for_ready

log = logging.getLogger(__name__)
old_config_map_data = {}

def pre_deploy(**kwargs):
    old_config_map_data = get_json("configmap", "MyConfigMap")['data']


def post_deploy(**kwargs):
    new_config_map_data = get_json("configmap", "MyConfigMap")['data']
    if new_config_map_data != old_config_map_data:
        oc("rollout", "dc/MyDeployment")
        wait_for_ready("dc", "MyDeployment")
```

### Images

You can use the `images` section in a `_cfg.yml` to instruct ocdeployer to import images and configure an ImageStream for them.

ocdeployer will run the following for each required image at deploy time.
```
oc import-image <istag> --from="<from>" --scheduled=true --confirm
```

If the image already exists, it will be re-imported (and therefore the image will be updated).

If the ImageStreamTag already exists, but the "from" URI has been updated, ocdeployer will run the following to re-configure the tag on the ImageStream:
```
oc tag --scheduled=true --source=docker <from> <istag>
```

If the same ImageStreamTag is defined more than once (whether in the same `_cfg.yml` or in various `_cfg.yml` files), it is not imported repeatedly. For this reason, ImageStreamTag's need to be uniquely named across service sets if they are intended to be deployed into the same namespace. 

The `images` section can be defined in two ways in `_cfg.yml`:

**Short format**

List the desired ImageStreamTag as the key, and the image's "from" URI as the value.

If no tag is specified, it is assumed that "latest" is the desired tag.
```
images:
- "cp-kafka:sometag": "confluentinc/cp-kafka"
```

**Long format** (`ocdeployer>=v4.3.0`)

* `istag` -- the desired ImageStreamTag
* `from` -- the image's external "from" URI
* `envs` -- lists envs this image should be imported for. The image will only be imported when ocdeployer is run with `--env` matching the envs given. If no envs are given, it is assumed it should be loaded in all envs.
```
images:
- istag: "cp-kafka:sometag"
  from: "confluentinc/cp-kafka"
  envs: ["qa", "prod"]
```

### Secrets

By default, `ocdeployer` will attempt to import secrets from the project `secrets` in OpenShift as well as by looking for secrets in the `./secrets` local directory. You can also use `--secrets-src-project` to copy secrets into your project from a different project in OpenShift, or use `--secrets-local-dir` to load secrets from OpenShift config files in a different directory.

If you set `--secrets-src-project` to be the same as the destination namespace, this effectively causes ocdeployer to simply validate that the secret is present in that namespace.

Any `.yaml`/`.json` files you place in the `secrets-local-dir` will be parsed and secrets will be pulled out of them and imported.
The files can contain a single secret (`kind: Secret`) OR a list of resources (`kind: List`)

An example secret `.yaml` file:
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

Secrets can be specified in `_cfg.yml` in two ways:

**Short format**

List just the name:
```
secrets:
- "name of secret"
```

**Long format** (`ocdeployer>=v4.3.0`)

List the name as well as:
* `link` -- lists service accounts the secret should be linked to using `oc secrets link`
* `envs` -- lists envs this secret should be imported for. The secret will only be imported when ocdeployer is run with `--env` matching the envs given. If no envs are given, it is assumed it should be loaded in all envs.
```
secrets:
- name: "name of secret"
  link: ["account1", "account2"]
  envs: ["qa", "prod"]
```

Note that any links existing for a secret will not be removed at deploy time -- `ocdeployer` will only add new links.


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
(venv) $ ocdeployer deploy -a --secrets-local-dir /tmp/secrets/ myproject
```

## Environment files
By default, the following parameters are passed to templates by ocdeployer at deploy time:

* 'NAMESPACE' corresponds to the project name selected on the CLI.
* 'SECRETS_PROJECT' corresponds to the secrets-src-project selected on the CLI (default: "secrets")


You can define "environment" files in two places with more customized variable information.

### Root environment files

In the root of your project, a directory called `env` can house environment yaml files which set variables at the root level of your project. Let's say we create a file called `env/myenv.yaml` which looks like this:

```yaml
global:
  # Values defined outside of the "parameters" section are intended to be evaluated during jinja2 processing
  some_var: false
  parameters:
    # Values defined as "parameters" are evaluated by 'oc process' as OpenShift template parameters
    VAR1: "applies to all components"
    VAR2: "also applies to all components"

set1:
  parameters:
    VAR2: "this overrides global VAR2 for only components in the 'set1' set"
    VAR3: "VAR3 applies to all components within the 'set1' service set"

set1/db:
  parameters:
    VAR2: "this overrides global VAR2, and 'set1' VAR2, for only the 'db' component"
    VAR4: "VAR4 only applies to 'db' component"
    # Using keyword {prompt} will cause ocdeployer to prompt for this variable's value at runtime.
    VAR5: "{prompt}"
```

This allows you to define your variables at a global level, at a "per service-set" level, or at a "per-component within a service-set" level. You can override variables with the same name at the "more granular levels" as well. If an OpenShift template does not have a variable defined in its "parameters" section, then that variable will be skipped at processing time. This allows you to define variables at a global level, but not necessarily have to define each variable as a parameter in every single template.

### Service set environment files

You can also define variables inside an `env` directory for each service set. Let's say we create a file at `templates/set1/env/myenv.yaml` which looks like this:

```yaml
global:
  # Values defined here apply to all components of service set 'set1'
  parameters:
    VAR1: "overrides the VAR1 set globally at root level"

db:
  parameters:
    VAR5: "overrides the VAR5 set on db component at root level"
```

The variable merging and variable overriding process works the same as for the root level env file. The service-set env file is merged with the root env file at processing time. But in addition, the values defined at the service-set level take precedence over any values defined at the root level.

### Selecting environment

Select your environment at runtime with the `-e` or `--env` command-line option, e.g.:
```
(venv) $ ocdeployer deploy -s myset -e myenv myproject
```
---
**NOTE**

In `ocdeployer<v4.0`, there was no support for env files within the service set, so only root environment files were used and the `-e/--env-file` argument was used to point to specific YAML file paths. This is still supported for backward compatibility.

---

### Overriding deploy config via environment file

The component key name `_cfg` is reserved in environment files. Data listed under this key can be structured in the same way as a `_cfg.yml` file, allowing you to add to or override values of the deloy config for different environments.

* A `_cfg` section defined in a base env file will be merged with values set in the base `_cfg`.
* A `_cfg` section defined in a service set's env file will be merged with values set in the `_cfg` for that service set.

As an example, let's say that you want to import an image only when deploying to the "qa" environment.

Your base config may look like this:
```
deploy_order:
  stage0:
    components: ["set1", "set2", "set3"]
```

Your service set config for `set1` may look like this:
```
images:
- image1: repo/image1:latest

deploy_order:
  stage0:
    components: ["component1", "component2"]
```

You could then define `templates/set1/env/qa.yml` with this setting:
```
_cfg:
  images:
  - image1: repo/image1:other-tag
  - image2: repo/image2:latest
```

This will cause the following to occur *only* when `--env qa` is selected at deploy time:
* the source image of the `image1` ImageStreamTag to be overridden to map to a new external tag (`other-tag` instead of `latest`)
* image2 to be imported

---

### Using multiple environment files

You can define multiple environments and merge them at deploy time. Example:

env1.yaml
```yaml
global:
  my_value: true
  my_other_value: false
```

env2.yaml
```yaml
global:
  my_value: false
  my_other_value: true
```

Running the following command:
```
(venv) $ ocdeployer deploy -s myset -e env1 -e env2 myproject
```

Results in env1.yaml and env2.yaml being merged. Since env1 is listed FIRST in the list, any matching parameter entries in this file will override those of env2. The result is a values file which looks like:

```yaml
global:
  my_value: true
  my_other_value: true
```

## Common usage

List the service sets available for you to deploy:
```
(venv) $ ocdeployer list-sets
Available service sets: ['platform', 'advisor', 'engine', 'vulnerability']
```

Example to deploy platform/engine service sets using "prod" env, and import secrets from "mysecretsproject":
```
(venv) $ ocdeployer deploy -s platform,engine -e prod --secrets-src mysecretsproject mynewproject
```

You can scale the cpu/memory requests/limits for all your resources using the `--scale-resources` flag:
```
(venv) $ ocdeployer deploy -s platform --scale-resources 0.5 mynewproject
```
This will multiply any configured resource requests/limits in your template by the desired factor. If you haven't configured a request/limit, it will not be scaled. If you scale by `0`, the resource configuration will be entirely removed from all items, causing your default Kubernetes limit ranges to kick in.


Delete everything (and that means pretty much everything, so be careful) from your project with:
```
(venv) $ ocdeployer wipe <openshift project name>
```

You can also delete everything matching a specific label:
```
(venv) $ ocdeployer wipe -l mylabel=myvalue <openshift project name>
```

## Known issues/needed improvements
* The scripts currently check to ensure deployments have moved to 'active' before exiting, however, they do not remediate any "hanging" or "stuck" builds/deployments.
* There is currently no way to "enforce" configuration -- i.e. delete stuff that isn't listed in the templates.
