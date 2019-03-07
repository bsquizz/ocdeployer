# ocdeployer
A tool which wraps the OpenShift command line tools to enable repeatable automated deployment of OpenShift templates. Allows you to re-create environments based on templates more efficiently. Given a set of OpenShift templates, you can create a simple config that allows you to:

* Repeatedly deploy the same templates to different OpenShift projects
* Define the order in which they should deploy via 'stages'
* Optionally wait for all deployments to reach 'active' state before continuing on to the next stage
* Define which 'images' should be imported to the project
* Define which secrets your services rely on, and import them either from a local dir, or from another project in OpenShift
* Split the templates up into "service sets" and deploy all sets, or specific sets
* Define dependencies (for example: service set 'A' requires service set 'B')
* Create environment files, which define parameters that should be set at template processing time, so you can deploy the same templates to different environments
* Specify multiple environment files at deploy time and merge them
* Use OpenShift templating along with jinja2 templating
* Create custom pre-deploy/deploy/post-deploy scripts in python if more granular control is neeed
* Quickly scale the resource request/limit defined in your templates.


**REQUIRES** OpenShift command line tools (the `oc` command)

You should log in to your project before deploying:

`$ oc login https://api.myopenshift --token=*************`


# Getting Started

## Details
`ocdeployer` relies on 4 pieces of information:
* A templates directory (default: ./templates) -- this houses your OpenShift YAML/JSON templates as well as special config files (named _cfg.yml). You can split your templates into folders, called service sets, and define a _cfg.yml inside each of these folders which takes care of deploying that specific service set. The base _cfg.yml defines the deploy order for all service sets, as well as any "global" secrets/images that should be imported that all services rely on.
* A custom scripts directory (default: ./custom). This is optional. Python scripts can be placed in here which can be run at pre-deploy/deploy/post-deploy stages for specific service sets.
* A secrets directory (default: ./secrets). This is optional. Openshift YAML files containing a secret or list of secrets can be placed in here. Service sets which require imported secrets can use the secrets in this directory.
* An environment file or files. This is optional. Defines parameters that should be passed to the templates. You can specify multiple environment files on the CLI at deploy time and the values will be merged.

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
  cache        Used for updating or deleting local template cache
  deploy       Deploy to project
  list-routes  List routes currently in the project
  list-sets    List service sets available in template dir
  process      Process templates but do not deploy
  wipe         Delete everything from project
```

#### Deploy command
The `deploy` command is used to push your templates into OpenShift.

Command usage:
```
(venv) $ ocdeployer deploy -h
Usage: ocdeployer deploy [OPTIONS] DST_PROJECT

  Deploy to project

Options:
  -a, --all                   Deploy all service sets
  -s, --sets TEXT             Comma,separated,list of specific service set
                              names to deploy
  -p, --pick TEXT             Pick a single component from a service set and
                              deploy that.  E.g. '-p myset/myvm'
  -k, --skip TEXT             Comma,separated,list of service_set/service_name
                              to skip
  -e, --env-file TEXT         Path to parameters config file (default: None).
                              Use this option multiple times to concatenate
                              config files
  -t, --template-dir TEXT     Template directory (default 'templates')
  --scale-resources FLOAT     Factor to scale configured cpu/memory resource
                              requests/limits by
  -f, --no-confirm            Do not prompt for confirmation
  --secrets-local-dir TEXT    Import secrets from local files in a directory
                              (default 'secrets')
  --secrets-src-project TEXT  Openshift project to import secrets from
                              (default: secrets)
  -i, --ignore-requires       Ignore the 'requires' statement in config files
                              and deploy anyway
  -u, --custom-dir TEXT       Specify custom deploy scripts directory (default
                              'custom')
  -l, --label TEXT            Adds a label to each deployed resource.  E.g.
                              '-l app=test'
  -h, --help                  Show this message and exit.
```

#### Process command

Use `process` to view the template data without actually deploying it. `process` has very similar options to `deploy`, but instead of pushing any configuration to OpenShift, it will simply parse the templates with jinja2 and openshift templating (i.e. `oc process`), substitute in the given variables/project name/etc., and then either print out the resulting configuration to stdout or save the resulting template files to a directory.

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

#### Cache command

The `cache` command provides a shortcut method to store a git repository of templates in your local application cache folder. If the cache has been initialized and the folder exists, `ocdeployer` uses this folder as its default location instead of the current working directory.

The implementation uses the `appdirs` cache folder, therefore...

* the default templates dir becomes:
  - Linux: `/home/<username>/.cache/ocdeployer/templates`
  - Mac: `/Users/USERNAME/Library/Application Support/ocdeployer/templates`
* the default scripts dir becomes:
  - Linux: `/home/<username>/.cache/ocdeployer/custom`
  - Mac: `/Users/USERNAME/Library/Application Support/ocdeployer/custom`
* the default secrets dir becomes:
  - Linux: `/home/<username>/.cache/ocdeployer/secrets`
  - Mac: `/Users/USERNAME/Library/Application Support/ocdeployer/secrets`


Note these defaults are only used IF the cache directory for `ocdeployer` is present.

---
## Template Configuration

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
    custom_deploy_logic: true

    # Indicates that any BuildConfigs deployed in these templates should be triggered
    # in post-deploy. This is useful since there is no ConfigChange trigger which
    # re-builds an existing BuildConfig. That that if custom_deploy_logic above is
    # 'true', then the post-deploy logic that runs this step will be overriden
    trigger_builds: false

    # Indicates how long post-deploy logic should take before timeout in sec
    # A null value can be handled differently depending on the post deploy logic,
    # but it is recommended that it means there is "no waiting" that will occur
    post_deploy_timeout: 300

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

6. Run `ocdeployer list-sets` and you should see your new component listed as a deployable service set.


### Custom Deploy Logic

By default, no pre_deploy/post_deploy is run, and the deploy logic is taken care of by the `ocdeployer.deploy.deploy_components` method. So, unless you are doing something complicated and require additional "python scripting" to handle your (pre/post)deploy logic, you don't need to worry about custom logic (and it's quite rare that you'd want to re-write the main deploy logic itself)

But let's say you want to perform some tasks prior to deploying your components, or after deploying your components. Custom scripts can be useful for this.

You can set `custom_deploy_logic` in your service set's `_cfg.yml` to `True`. You should then create a custom deploy script in the `custom` dir of your project with a name that matches your service set -- e.g. `deploy_myservice.py`. Inside this script you can define 3 methods:

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
* `wait`: boolean, used to determine whether the deploy logic should wait for things to "finish"
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


#### Example 1
Let's say that after you deploy your components, you want to trigger a build on any build configurations you pushed (actually, this can easily be handled by setting `trigger_builds` to `True` in your service set `_cfg.yml` -- but play along).

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

#### Example 2
Let's say that when any ConfigMaps in your project update, you want to trigger a new rollout of a deployment. You could do that by tracking the state of the ConfigMap before deploying and comparing it to the state after.

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
(venv) $ ocdeployer deploy -a --secrets-local-dir /tmp/secrets/ myproject
```

## Environment file
By default, the 'NAMESPACE' parameter is passed to all templates which corresponds to the project name. You can also define an "environment"
file with more detailed variable information. Here is an example:

```yaml
global:
  # Values defined outside of the "parameters" section are intended to be evaluated during jinja2 processing
  some_var: false
  parameters:
    # Values defined as "parameters" are evaluated by 'oc process' as OpenShift template parameters
    VAR1: "applies to all components"
    VAR2: "also applies to all components"

advisor:
  parameters:
    VAR2: "this overrides global VAR2 for only components in the advisor set"
    VAR3: "VAR3 applies to all components within the advisor service set"

advisor/advisor-db:
  parameters:
    VAR2: "this overrides global VAR2, and advisor VAR2, for only the advisor-db component"
    VAR4: "VAR4 only applies to advisor-db"
    # Using keyword {prompt} will cause ocdeployer to prompt for this variable's value at runtime.
    VAR5: "{prompt}"
```

This allows you to define your variables at a global level, at a "per service-set" level, or at a "per-component within a service-set" level. You can override variables with the same name at the "more granular levels" as well. If an OpenShift template does not have a variable defined in its "parameters" section, then that variable will be skipped at processing time. This allows you to define variables at a global level,
but not necessarily have to define each variable as a parameter in every single template.

Select your environment file at runtime with the `-e` or `--env-file` command-line option, e.g.:
```
(venv) $ ocdeployer deploy -s myset -e my_env_file.yml myproject
```

You can define multiple environment files and merge them at deploy time. Example:

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
```

Running the following command:
```
(venv) $ ocdeployer deploy -s myset -e env1.yaml -e env2.yaml myproject
```

Results in env1.yaml and env2.yaml being merged. Since env2 is listed later in the list, any matching parameter entries in this file will override those of env1. The result is a values file which looks like:

```yaml
global:
  my_value: false
  my_other_value: false
```

## Common usage

List the service sets available for you to deploy:
```
(venv) $ ocdeployer list-sets
Available service sets: ['platform', 'advisor', 'engine', 'vulnerability']
```

Example to deploy platform, engine, and import secrets from "mysecretsproject":
```
(venv) $ ocdeployer deploy -s platform,engine --secrets-src mysecretsproject mynewproject
```

You can scale the cpu/memory requests/limits for all your resources using the `--scale-resources` flag:
```
(venv) $ ocdeployer deploy -s platform --scale-resources 0.5 mynewproject
```
This will multiply any configured resource requests/limits in your template by the desired factor. If
you haven't configured a request/limit, it will not be scaled. If you scale by `0`, the resource
configuration will be entirely removed from all items, causing your default Kubernetes limit
ranges to kick in.


Delete everything (and that means pretty much everything, so be careful) from your project with:
```
(venv) $ ocdeployer wipe <openshift project name>
```

You can also delete everything matching a specific label:
```
(venv) $ ocdeployer wipe -l mylabel=myvalue <openshift project name>
```

## Known issues/needed improvements
* The scripts currently check to ensure deployments have moved to 'active' before exiting, however, they do not remediate any "hanging" or "stuck" builds/deployments. Work on that will be coming soon...
* There is currently no way to "enforce" configuration -- i.e. delete stuff that isn't listed in the templates.