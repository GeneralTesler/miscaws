from typing import Union


def merge_dicts(dict1: Union[dict, None], dict2: Union[dict, None]):
    """merge two dict
    also handles one or more empty dicts
    """
    if not dict1 and not dict2:
        return {}
    if not dict1:
        return dict2
    if not dict2:
        return dict1
    return {**dict1, **dict2}
