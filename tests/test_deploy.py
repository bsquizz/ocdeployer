import ocdeployer
import ocdeployer.deploy

import pytest

def runner(variables_data):
    return ocdeployer.deploy.DeployRunner(
               None,
               "test-project",
               variables_data,
               None,
               None,
               None,
               None
           )

def test__get_variables_sanity():
    variables_data = {
        "service": {
            "enable_routes": False,
            "enable_db": False,
            "parameters": {
                "STUFF": "things"
            }
        }
    }
    expected = {
        "enable_routes": False,
        "enable_db": False,
        "parameters": {
            "STUFF": "things",
            "NAMESPACE": "test-project"
        }
    }
    assert runner(variables_data)._get_variables("service", []) == expected

def test__get_variables_merge_from_global():
    variables_data = {
        "global": {
            "global_variable": "global-value",
            "parameters": {
                "GLOBAL": "things"
            }
        },
        "service": {
            "service_variable": True,
            "parameters": {
                "STUFF": "service-stuff"
            }
        },
        "service/component": {
            "component_variable": "component",
            "parameters": {
                "COMPONENT": "component-param"
            }
        }
    }

    expected = {
        "component_variable": "component",
        "global_variable": "global-value",
        "service_variable": True,
        "parameters": {
            "COMPONENT": "component-param",
            "GLOBAL": "things",
            "STUFF": "service-stuff",
            "NAMESPACE": "test-project"
        }
    }
    assert runner(variables_data)._get_variables("service", "component") == expected

def test__get_variables_service_overwrite_parameter():
    variables_data = {
        "global": {
            "parameters": {
                "STUFF": "things"
            }
        },
        "service": {
            "parameters": {
                "STUFF": "service-stuff"
            }
        }
    }
    expected = {
        "parameters": {
            "STUFF": "service-stuff",
            "NAMESPACE": "test-project"
        }
    }
    assert runner(variables_data)._get_variables("service", []) == expected

def test__get_variables_service_overwrite_variable():
    variables_data = {
        "global": {
            "enable_db": False
        },
        "service": {
            "enable_db": True
        }
    }
    expected = {
        "enable_db": True,
        "parameters": {
            "NAMESPACE": "test-project"
        }
    }
    assert runner(variables_data)._get_variables("service", []) == expected

def test__get_variables_component_overwrite_parameter():
    variables_data = {
        "global": {
            "parameters": {
                "STUFF": "things"
            }
        },
        "service": {
            "parameters": {
                "THINGS": "service-things"
            }
        },
        "service/component": {
            "parameters": {
                "THINGS": "component-things"
            }
        }
    }
    expected = {
        "parameters": {
            "STUFF": "things",
            "THINGS": "component-things",
            "NAMESPACE": "test-project"
        }
    }
    assert runner(variables_data)._get_variables("service", "component") == expected

def test__get_variables_component_overwrite_variable():
    variables_data = {
        "global": {
            "enable_routes": False
        },
        "service": {
            "enable_db": True
        },
        "service/component": {
            "enable_db": False
        }
    }
    expected = {
        "enable_routes": False,
        "enable_db": False,
        "parameters": {
            "NAMESPACE": "test-project"
        }
    }
    assert runner(variables_data)._get_variables("service", "component") == expected
