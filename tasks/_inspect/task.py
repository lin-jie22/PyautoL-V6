from typing import Any, Collection, Iterable, Optional, Tuple, Type, Union


def get_object_types_mro(obj: Union[object, Type[Any]]) -> Tuple[type, ...]:
    """Returns the MRO of an object's class, or of the object itself if it's a class."""
    if not hasattr(obj, '__mro__'):
        obj = type(obj)
    return getattr(obj, '__mro__', ())
def get_object_types_mro_as_strings(obj: object) -> Collection[str]:
    """
    Returns the MRO of an object's class as full qualified names, or of the object itself if it's a class.

    Examples:
        `object_types_mro_as_strings(JSONDecoder)` will return `['json.decoder.JSONDecoder', 'builtins.object']`
    """
    return [f"{getattr(type_, '__module__', '')}.{getattr(type_, '__qualname__', '')}" for type_ in get_object_types_mro(obj)]
def is_object_one_of_types(obj: object, fully_qualified_types_names: Collection[str]) -> bool:
    """
    Returns `True` if the given object's class (or the object itself, if it's a class) has one of the
    fully qualified names in its MRO.
    """
    for type_name in get_object_types_mro_as_strings(obj):
        if type_name in fully_qualified_types_names:
            return True
    return False
