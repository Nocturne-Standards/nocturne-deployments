"""Bundled JSON schemas and a small validator for the subset they use.

Kept dependency-free so ops machines and CI can run the tool with plain Python.
Supported keywords: type, const, enum, required, properties, additionalProperties,
propertyNames, pattern, minLength, minimum, maximum, items, uniqueItems, allOf, anyOf,
if/then (inside allOf), and local ``$ref`` (``#/...``) into ``$defs``. Annotations
($schema, $id, title, description) are ignored. ``unsupported`` lists any other keyword
a schema uses, so a schema written by someone else is never half-checked in silence.
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
    "nocturne.upstream-lock.v1": "upstream-lock.v1.json",
    "nocturne.upstream-consumers.v1": "upstream-consumers.v1.json",
    "nocturne.upstream-history.v1": "upstream-history.v1.json",
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


def _same(a: Any, b: Any) -> bool:
    """JSON equality: ``true`` is not ``1`` (Python's ``==`` says it is)."""
    if isinstance(a, bool) or isinstance(b, bool):
        return isinstance(a, bool) and isinstance(b, bool) and a == b
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    return a == b


def _resolve(ref: str, root: dict[str, Any]) -> dict[str, Any]:
    if not ref.startswith("#"):
        raise ValueError(f"only local $ref is supported, not {ref!r}")
    node: Any = root
    for part in ref[1:].split("/")[1:]:
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or part not in node:
            raise ValueError(f"$ref {ref!r} does not resolve")
        node = node[part]
    return node


def _errors(value: Any, schema: dict[str, Any], path: str, root: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    if "$ref" in schema:
        errors.extend(_errors(value, _resolve(schema["$ref"], root), path, root))

    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, t) for t in types):
            return errors + [f"{path}: expected {'/'.join(types)}, got {type(value).__name__}"]

    if "const" in schema and not _same(value, schema["const"]):
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and not any(_same(value, item) for item in schema["enum"]):
        errors.append(f"{path}: {value!r} not in {schema['enum']}")

    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{path}: {value!r} does not match {schema['pattern']}")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than {schema['minLength']}")

    if _is_type(value, "number"):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above {schema['maximum']}")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing {key!r}")
        props: dict[str, Any] = schema.get("properties", {})
        extra = schema.get("additionalProperties", True)
        for key, item in value.items():
            if "propertyNames" in schema:
                errors.extend(_errors(key, schema["propertyNames"], f"{path} key {key!r}", root))
            if key in props:
                errors.extend(_errors(item, props[key], f"{path}.{key}", root))
            elif extra is False:
                errors.append(f"{path}: unexpected key {key!r}")
            elif isinstance(extra, dict):
                errors.extend(_errors(item, extra, f"{path}.{key}", root))

    if isinstance(value, list):
        if "items" in schema:
            for i, item in enumerate(value):
                errors.extend(_errors(item, schema["items"], f"{path}[{i}]", root))
        if schema.get("uniqueItems") is True:
            for i, item in enumerate(value):
                if any(_same(item, earlier) for earlier in value[:i]):
                    errors.append(f"{path}[{i}]: duplicate item")

    for sub in schema.get("allOf", []):
        if "if" in sub:
            if not _errors(value, sub["if"], path, root):
                errors.extend(_errors(value, sub.get("then", {}), path, root))
        else:
            errors.extend(_errors(value, sub, path, root))

    if "anyOf" in schema:
        branches = [_errors(value, sub, path, root) for sub in schema["anyOf"]]
        if all(branches):
            detail = " | ".join(b[0] for b in branches)
            errors.append(f"{path}: matches none of anyOf ({detail})")

    return errors


# Keywords the validator acts on, and annotations it may ignore.
_SUPPORTED = {
    "type", "const", "enum", "required", "properties", "additionalProperties", "propertyNames",
    "pattern", "minLength", "minimum", "maximum", "items", "uniqueItems", "allOf", "anyOf", "$ref", "$defs",
}
_ANNOTATIONS = {"$schema", "$id", "title", "description"}


def unsupported(schema: Any, path: str = "#", root: Any = None) -> list[str]:
    """Keywords in ``schema`` this validator would ignore, as ``path: keyword``; empty means fully checked."""
    root = schema if root is None else root
    if isinstance(schema, bool):
        return []
    if not isinstance(schema, dict):
        return [f"{path}: not a schema"]
    out = [f"{path}: {key}" for key in schema if key not in _SUPPORTED | _ANNOTATIONS]
    for key in ("properties", "$defs"):
        for name, sub in schema.get(key, {}).items():
            out += unsupported(sub, f"{path}/{key}/{name}", root)
    for key in ("additionalProperties", "items", "propertyNames"):
        if key in schema:
            out += unsupported(schema[key], f"{path}/{key}", root)
    for i, sub in enumerate(schema.get("anyOf", [])):
        out += unsupported(sub, f"{path}/anyOf/{i}", root)
    for i, sub in enumerate(schema.get("allOf", [])):
        if isinstance(sub, dict) and "if" in sub:
            # if/then is supported only as an allOf member, nothing else beside it.
            out += [f"{path}/allOf/{i}: {k}" for k in sub if k not in ("if", "then")]
            out += unsupported(sub["if"], f"{path}/allOf/{i}/if", root)
            out += unsupported(sub.get("then", {}), f"{path}/allOf/{i}/then", root)
        else:
            out += unsupported(sub, f"{path}/allOf/{i}", root)
    if "$ref" in schema:
        try:
            _resolve(schema["$ref"], root)
        except ValueError as exc:
            out.append(f"{path}: {exc}")
    return out


def validate(value: Any, schema_id: str, where: str = "$") -> list[str]:
    """Return a list of human-readable errors; empty means valid."""
    schema = load_schema(schema_id)
    return _errors(value, schema, where, schema)


def validate_with(value: Any, schema: dict[str, Any], where: str = "$") -> list[str]:
    """Validate against a schema given as data (an upstream's own schema)."""
    return _errors(value, schema, where, schema)
