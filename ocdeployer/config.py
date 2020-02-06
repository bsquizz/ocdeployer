from .secrets import parse_config as parse_secrets_config
from .images import parse_config as parse_images_config
from .utils import object_merge


def merge_list_of_dicts(old, new, key):
    """
    Merge a list of dictionary items based on a specific key.

    Dictionaries inside the list with a matching key get merged together.

    Assumes that a value for the given key is unique and appears only once.

    Example:
    list1 = [{"name": "one", "data": "stuff"}, {"name": "two", "data": "stuff2"}]
    list2 = [{"name": "one", "data": "newstuff"}]

    merge_list_of_dicts(list1, list2) returns:
    [{"name": "one", "data": "newstuff"}, {"name": "two", "data": "stuff2"}]
    """
    for old_item in reversed(old):
        matching_val = old_item[key]
        for new_item in new:
            if new_item[key] == matching_val:
                object_merge(old_item, new_item)
                break
        else:
            new.append(old_item)
    return new


def merge_cfgs(old, new):
    """
    Merges two _cfg definitions together.

    This is similar to an object_merge but with special processing of the 'images' and 'secrets'
    since these are lists of dictionaries

    object_merge simply merges items from an old & new list together. Instead, if 2 dictionaries
    items in the lists are defining data for the same image or secret, we want to merge the data of
    those dictionaries together.
    """
    old_images = parse_images_config(old)
    new_images = parse_images_config(new)
    old_secrets = parse_secrets_config(old)
    new_secrets = parse_secrets_config(new)

    # First do a standard object_merge, we'll then replace 'images' and 'secrets'
    object_merge(old, new)
    # Now merge the list of dictionaries together using their identifying key
    new["images"] = merge_list_of_dicts(old_images, new_images, key="istag")
    new["secrets"] = merge_list_of_dicts(old_secrets, new_secrets, key="name")

    return new
