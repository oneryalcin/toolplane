"""Capability metadata and function introspection."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import NoneType, UnionType
from typing import Annotated, Any, get_args, get_origin, get_type_hints


JsonSchema = dict[str, Any]


@dataclass(frozen=True)
class Capability:
    name: str
    callable: Callable[..., Any]
    description: str
    parameters: JsonSchema
    returns: JsonSchema | None
    tags: frozenset[str]
    source: str = "python"
    aliases: frozenset[str] = field(default_factory=frozenset)
    hidden: bool = False
    namespace: str | None = None
    namespace_member: str | None = None

    def to_schema(self) -> dict[str, Any]:
        schema: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.parameters,
            "source": self.source,
        }
        if self.returns is not None:
            schema["outputSchema"] = self.returns
        if self.tags:
            schema["tags"] = sorted(self.tags)
        if self.aliases:
            schema["aliases"] = sorted(self.aliases)
        if self.hidden:
            schema["hidden"] = True
        if self.namespace is not None and self.namespace_member is not None:
            schema["namespace"] = {
                "name": self.namespace,
                "member": self.namespace_member,
            }
        return schema

    @property
    def searchable_text(self) -> str:
        parts = [
            self.name,
            self.description,
            self.source,
            self.namespace or "",
            self.namespace_member or "",
            *self.tags,
            *self.aliases,
        ]
        properties = self.parameters.get("properties", {})
        if isinstance(properties, Mapping):
            for name, schema in properties.items():
                parts.append(str(name))
                if isinstance(schema, Mapping):
                    parts.append(str(schema.get("description", "")))
        return " ".join(part for part in parts if part)


def capability_from_function(
    fn: Callable[..., Any],
    *,
    name: str | None = None,
    description: str | None = None,
    tags: set[str] | frozenset[str] | None = None,
    source: str = "python",
    aliases: set[str] | frozenset[str] | None = None,
    hidden: bool = False,
    namespace: str | None = None,
    namespace_member: str | None = None,
) -> Capability:
    """Build a capability from a Python callable."""
    capability_name = name or fn.__name__
    doc = inspect.getdoc(fn) or ""
    capability_description = description if description is not None else _summary(doc)
    hints = get_type_hints(fn, include_extras=True)
    signature = inspect.signature(fn)
    return_annotation = hints.get("return", signature.return_annotation)

    return Capability(
        name=capability_name,
        callable=fn,
        description=capability_description,
        parameters=_parameters_schema(signature, hints),
        returns=None
        if return_annotation is inspect.Signature.empty
        else _type_to_schema(return_annotation),
        tags=frozenset(tags or ()),
        source=source,
        aliases=frozenset(aliases or ()),
        hidden=hidden,
        namespace=namespace,
        namespace_member=namespace_member,
    )


def _summary(docstring: str) -> str:
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _parameters_schema(
    signature: inspect.Signature,
    hints: Mapping[str, Any],
) -> JsonSchema:
    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, parameter in signature.parameters.items():
        if parameter.kind in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        }:
            continue
        annotation = hints.get(name, parameter.annotation)
        properties[name] = _type_to_schema(annotation)
        if parameter.default is inspect.Parameter.empty:
            required.append(name)

    schema: JsonSchema = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


def _type_to_schema(annotation: Any) -> JsonSchema:
    if annotation is inspect.Signature.empty or annotation is Any:
        return {}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Annotated:
        base, *metadata = args
        schema = _type_to_schema(base)
        for item in metadata:
            if isinstance(item, str):
                schema = {**schema, "description": item}
                break
        return schema

    if origin in {list, tuple, set, frozenset}:
        item_type = args[0] if args else Any
        return {"type": "array", "items": _type_to_schema(item_type)}

    if origin is dict:
        return {"type": "object"}

    if origin is UnionType or str(origin) == "typing.Union":
        return _union_schema(args)

    if annotation is None or annotation is NoneType:
        return {"type": "null"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is bytes:
        return {"type": "string", "contentEncoding": "base64"}

    return {"type": "object"}


def _union_schema(args: tuple[Any, ...]) -> JsonSchema:
    schemas = [_type_to_schema(arg) for arg in args]
    if len(schemas) == 2 and {"type": "null"} in schemas:
        non_null = next(schema for schema in schemas if schema != {"type": "null"})
        return {"anyOf": [non_null, {"type": "null"}]}
    return {"anyOf": schemas}
