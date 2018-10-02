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
