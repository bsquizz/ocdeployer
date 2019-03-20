import ocdeployer
import ocdeployer.utils as utils

import pytest

def test_object_merge_merges_lists():
    edit = [1, 3]
    add = [2]

    utils.object_merge(add, edit)

    assert edit == [2, 1, 3]

def test_obect_merge_merges_dicts():
    edit = {"thing": "stuff"}
    add = {"test": "add"}

    utils.object_merge(add, edit)

    assert edit == {"thing": "stuff", "test": "add"}

def test_object_merge_keeps_original_dict_keys():
    edit = {"thing": 3}
    add = {"thing": 1}

    utils.object_merge(add, edit)

    assert edit == {"thing": 3}

def test_object_merge_recursively_merges_dicts():
    edit = {"things": {"stuff": 1}}
    add = {"things": {"new": 2}}

    utils.object_merge(add, edit)

    assert edit == {"things": {"stuff": 1, "new": 2}}
