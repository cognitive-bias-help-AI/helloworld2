"""Validation for JSON-native acquisition values without coercion."""

from math import isfinite

from app.store.errors import StoreError


def validate_json_native(value: object, *, path: str) -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if isinstance(value, int):
        return
    if isinstance(value, float):
        if isfinite(value):
            return
        raise StoreError(f"{path} must contain only finite JSON-native floats")
    if isinstance(value, list):
        for index, item in enumerate(value):
            validate_json_native(item, path=f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise StoreError(f"{path} must contain only string JSON-native keys")
            validate_json_native(item, path=f"{path}.{key}")
        return
    raise StoreError(f"{path} contains a non-JSON-native value: {type(value).__name__}")
