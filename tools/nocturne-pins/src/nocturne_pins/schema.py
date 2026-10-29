"""Bundled JSON schemas and a small validator for the subset they use.

Kept dependency-free so ops machines and CI can run the tool with plain Python.
Supported keywords: type, const, enum, required, properties, additionalProperties,
pattern, minLength, minimum, items, allOf, if/then.
"""

from __future__ import annotations

import json
import re
from functools import cache
from importlib import resources
from typing import Any

SCHEMA_FILES: dict[str, str] = {
    "nocturne.pins.v2": "pins.v2.json",
    "nocturne.history.v1": "history.v1.json",
    "nocturne.index.v1": "index.v1.json",
}

_TYPES: dict[str, tuple[type, ...]] = {
    "object": (dict,),
    "array": (list,),
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "null": (type(None),),
}


@cache
def load_schema(schema_id: str) -> dict[str, Any]:
    name = SCHEMA_FILES[schema_id]
    text = resources.files("nocturne_pins").joinpath("schemas", name).read_text(encoding="utf-8")
    return json.loads(text)


def schema_text(schema_id: str) -> str:
    name = SCHEMA_FILES[schema_id]
    return resources.files("nocturne_pins").joinpath("schemas", name).read_text(encoding="utf-8")


def _is_type(value: Any, type_name: str) -> bool:
    # bool is an int subclass in Python; JSON keeps them apart.
    if type_name in ("integer", "number") and isinstance(value, bool):
        return False
    return isinstance(value, _TYPES[type_name])


def _errors(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            return [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not in {schema['enum']}")

    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")

    if _is_type(value, "number") and "minimum" in schema and value < schema["minimum"]:
        errors.append(f"{path}: below {schema['minimum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key!r}")
        props: dict[str, Any] = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in props:
                errors.extend(_errors(item, props[key], f"{path}.{key}"))
            elif extra is False:
                errors.append(f"{path}: unexpected key {key!r}")
            elif isinstance(extra, dict):
                errors.extend(_errors(item, extra, f"{path}.{key}"))

    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(_errors(item, schema["items"], f"{path}[{i}]"))

    for sub in schema.get("allOf", []):
        if "if" in sub:
            if not _errors(value, sub["if"], path):
                errors.extend(_errors(value, sub.get("then", {}), path))
        else:
            errors.extend(_errors(value, sub, path))

    return errors


def validate(value: Any, schema_id: str, where: str = "$") -> list[str]:
    """Return a list of human-readable errors; empty means valid."""
    return _errors(value, load_schema(schema_id), where)
