# Example

An example project that could be deployed with `ocdeployer`, along with explanations for how this configuration is set up.

Note that this is just deploying a few random example templates for the sake of teaching a lesson :)

Suppose we have a project that consists of two groups of services:
* set1 -- which has nginx and a postgres DB
* set2 -- which has a ruby "hello-world" app, and a MySQL DB

`set2` relies on `set1` as a dependency before it can be deployed. In addition, we want to be able to re-use the same templates to
deploy to two different environments (QA & Prod). In addition, we have a few "special things" we want to do after deploying `set2` -- in other words, simply running `oc process` on the templates, `oc apply` to install them, and waiting for their `DeploymentConfig`
to switch to `active` isn't enough. We have some extra things to do.

## Implementing the example

1) In our `templates` directory, we create a `_cfg.yml` to define the order in which each set should be deployed. We could deploy
`set1` and `set2` in the same stage, but remember that `set2` depends on `set1` being deployed first, so we'll use two separate stages here.
A `stage` denotes a boundary -- we won't move on to the next stage until all components in the current stage are ready. If `return_immediately`
is set to `True`, however, we won't wait for the deployments to reach `active` state and we'll move right on to the next stage.

    ```yaml
    deploy_order:
    # Defines the order components should be deployed in.
    # If you specify components with "-c", only those components will be deployed, but
    # the order in which they are deployed will be preserved.
    stage0:
        return_immediately: False
        # You can optionally define the timeout on a stage. We'll wait <timeout> sec for deployments to become active before timing out.
        # The default is 300 sec
        #timeout: 400
        components:
        - "set1"
    stage1:
        return_immediately: False
        components:
        - "set2"
    ```
2) In the `templates` directory, we create two folders to represent each service set: `templates/set1` and `templates/set2`. We'll put the OpenShift YAML templates files into their appropriate directory: `nginx.yml` and `postgres.yml` go in `set1`, `mysql.yml` and `ruby-app.yml` go in `set2`. In addition, each service set folder gets its own `_cfg.yml` to define how that service set is deployed.

    `templates/set1/_cfg.yml` looks like this:

    ```yaml
    images:
      # Images our configs rely on. We will run 'oc import-image' on these.
      # The key is the ImageStream name. The value is the docker image to pull.
      nginx: "nginx"
      postgresql: "postgresql"

    secrets:
    # Names of secrets these templates rely on that we need to import
    - "postgres-dbsecrets"

    deploy_order:
      stage0:
        components:
        # We'll deploy both components in stage zero
        - "mysql"
        - "postgres"
    ```

    `templates/set2/_cfg.yml` looks like this:

    ```yaml
    requires:
    # This set cannot be deployed until 'set1' is deployed
    - "set1"

    images:
      # Images our configs rely on. We will run 'oc import-image' on these.
      # The key is the ImageStream name. The value is the docker image to pull.
      origin-custom-docker-builder: "openshift/origin-custom-docker-builder"
      mysql-57-centos7: "centos/mysql-57-centos7"

    # Indicates we are making use of a custom pre-deploy/deploy/post-deploy script
    custom_deploy_logic: True

    secrets:
    # Names of secrets these templates rely on that we need to import
    - "mysql-dbsecret"

    deploy_order:
      # We will deploy mysql in the first stage.
      # Once it switches to 'active', we will move on to deploy ruby-app
      stage0:
        components:
        - "mysql"
      stage1:
        components:
        - "ruby-app"
    ```

3) `set2` contains custom deploy logic, in `custom/deploy_set2.py`. There is a `post_deploy` method defined in there to do some "extra work" after the deploy has occurred. As an example, in this script we patch a `ConfigMap` with updated info after the `nginx1` service has been deployed and we are able to see what the frontend's auto-generated route is.

    ```python
    import json
    import time

    from ocdeployer.common.utils import oc, wait_for_ready, get_json, get_routes, rollout

    def post_deploy(**kwargs):
        map_name = "nginx-index-html"
        deployment_name = "nginx1"
        configmap = get_json("configmap", map_name)
        api_route = get_routes()[deployment_name]
        current = configmap["data"]["index.html"]
        configmap["data"]["index.html"] = current.replace("{{ROUTE}}", api_route)
        oc("patch", "configmap", map_name, p=json.dumps(configmap), _exit_on_err=False)

        rollout(deployment_name)
    ```

4) The secrets these templates rely on which are too sensitive to store in the template data itself are kept in a yaml file (or copied into it at deploy runtime) in the `secrets` directory.
See [mysql-secrets.yml](secrets/mysql-secrets.yml) and [postgres-secrets.yml](secrets/postgres-secrets.yml).

5) We define two environment files:
* [prod-env.yml](prod-env.yml) -- defines the variables that apply when deploying these services to production
* [qa-env.yml](qa-env.yml) -- defines the variables that apply when deploying these services to a QA env

## Running the deploy

Now, we can do the following:

Deploy only set1 to project 'myproject' using QA env settings

`$ ocdeployer deploy -s set1 -e qa-env.yml myproject`

Deploy all service sets to project 'myproject' using production env settings

`$ ocdeployer deploy -a -e prod-env.yml myproject`

If we had more sets, you could deploy only set1 and set2 with the below command. Note that even though we have listed `set2` first, it will still get deployed in the order the service sets are listed in `_cfg.yml`

`$ ocdeployer deploy -s set2,set1 -e prod-env.yml myrpoject`

## High-level steps of the deploy process

`ocdeployer` essentially does the following for each service set as it deploys them:
1) Runs `oc import-image` for any images listed in the `_cfg.yml`. NOTE: if image streams with the same name/tag already exist in the project, they will not be re-imported.
2) Imports any needed secrets from the secrets local dir, or from a separate OpenShift project. NOTE: if any secrets exist with the same name in the project, they will be overwritten.
3) Runs custom pre-deploy logic, if any is defined.
4) For each stage, it deploys the components in the configured order. If the default `deploy` logic is not overwritten by a custom deploy method:
* the template is run through jinja2 processing first. jinja2 processing will use any values in the env.yaml that are not defined as 'parameters'
* the template is then processed via `oc process -f` and the `parameters` defined in the env file are passed in. If a parameter exists in the env file but it is NOT defined in the template, it will not be passed in.
* `oc apply` is run for the processed template. This means you can "re-deploy" over an existing deployment, and if items already exist the config for them is just overwritten.
5) Waits for any `DeploymentConfigs` that were just configured to reach "active" state (it waits for all components in a stage in parallel), then moves on to the next stage.
6) When all stages are completed in the service set, it runs custom post-deploy logic for the service set, if any is defined.
