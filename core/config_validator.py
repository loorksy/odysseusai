"""
C104 — Config Validator
Schema-driven configuration validation with type coercion,
default injection, and human-readable error reporting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Union


@dataclass
class FieldSpec:
    name: str
    type: type
    required: bool = True
    default: Any = None
    choices: Optional[list] = None
    min_value: Optional[float] = None
    max_value: Optional[float] = None
    min_length: Optional[int] = None
    max_length: Optional[int] = None
    pattern: Optional[str] = None  # regex for strings
    validator: Optional[Callable[[Any], bool]] = None
    description: str = ""


@dataclass
class ValidationError:
    field: str
    message: str
    value: Any = None


@dataclass
class ValidationResult:
    valid: bool
    errors: list[ValidationError] = field(default_factory=list)
    coerced: dict = field(default_factory=dict)

    def raise_if_invalid(self) -> None:
        if not self.valid:
            msgs = "; ".join(f"{e.field}: {e.message}" for e in self.errors)
            raise ValueError(f"Config validation failed: {msgs}")


class ConfigSchema:
    """
    Declarative config schema.

    Usage::

        schema = ConfigSchema()
        schema.field("model", str, required=True, choices=["gpt-4", "gpt-4o"])
        schema.field("temperature", float, default=0.7, min_value=0.0, max_value=2.0)
        result = schema.validate({"model": "gpt-4"})
        result.raise_if_invalid()
        config = result.coerced
    """

    def __init__(self):
        self._fields: dict[str, FieldSpec] = {}
        self._extra_ok: bool = False

    def field(
        self,
        name: str,
        ftype: type,
        required: bool = True,
        default: Any = None,
        choices: Optional[list] = None,
        min_value: Optional[float] = None,
        max_value: Optional[float] = None,
        min_length: Optional[int] = None,
        max_length: Optional[int] = None,
        pattern: Optional[str] = None,
        validator: Optional[Callable[[Any], bool]] = None,
        description: str = "",
    ) -> "ConfigSchema":
        self._fields[name] = FieldSpec(
            name=name,
            type=ftype,
            required=required,
            default=default,
            choices=choices,
            min_value=min_value,
            max_value=max_value,
            min_length=min_length,
            max_length=max_length,
            pattern=pattern,
            validator=validator,
            description=description,
        )
        return self

    def allow_extra(self, allow: bool = True) -> "ConfigSchema":
        self._extra_ok = allow
        return self

    def validate(self, data: dict) -> ValidationResult:
        errors: list[ValidationError] = []
        coerced: dict = {}

        if not self._extra_ok:
            for key in data:
                if key not in self._fields:
                    errors.append(ValidationError(field=key, message="Unknown field", value=data[key]))

        for name, spec in self._fields.items():
            raw = data.get(name, _MISSING)

            if raw is _MISSING:
                if spec.required and spec.default is None:
                    errors.append(ValidationError(field=name, message="Required field missing"))
                    continue
                coerced[name] = spec.default
                continue

            # Type coercion
            value = raw
            if not isinstance(raw, spec.type):
                try:
                    value = spec.type(raw)
                except (ValueError, TypeError):
                    errors.append(ValidationError(
                        field=name,
                        message=f"Cannot coerce {type(raw).__name__!r} to {spec.type.__name__!r}",
                        value=raw,
                    ))
                    continue

            # Choices
            if spec.choices is not None and value not in spec.choices:
                errors.append(ValidationError(
                    field=name,
                    message=f"Must be one of {spec.choices}",
                    value=value,
                ))

            # Numeric range
            if spec.min_value is not None and isinstance(value, (int, float)) and value < spec.min_value:
                errors.append(ValidationError(field=name, message=f"Must be >= {spec.min_value}", value=value))
            if spec.max_value is not None and isinstance(value, (int, float)) and value > spec.max_value:
                errors.append(ValidationError(field=name, message=f"Must be <= {spec.max_value}", value=value))

            # String length
            if spec.min_length is not None and isinstance(value, str) and len(value) < spec.min_length:
                errors.append(ValidationError(field=name, message=f"Min length {spec.min_length}", value=value))
            if spec.max_length is not None and isinstance(value, str) and len(value) > spec.max_length:
                errors.append(ValidationError(field=name, message=f"Max length {spec.max_length}", value=value))

            # Regex pattern
            if spec.pattern and isinstance(value, str) and not re.fullmatch(spec.pattern, value):
                errors.append(ValidationError(field=name, message=f"Must match pattern '{spec.pattern}'", value=value))

            # Custom validator
            if spec.validator:
                try:
                    ok = spec.validator(value)
                    if not ok:
                        errors.append(ValidationError(field=name, message="Custom validation failed", value=value))
                except Exception as e:
                    errors.append(ValidationError(field=name, message=f"Validator raised: {e}", value=value))

            coerced[name] = value

        return ValidationResult(valid=len(errors) == 0, errors=errors, coerced=coerced)

    def describe(self) -> dict:
        return {
            name: {
                "type": spec.type.__name__,
                "required": spec.required,
                "default": spec.default,
                "description": spec.description,
                "choices": spec.choices,
            }
            for name, spec in self._fields.items()
        }


_MISSING = object()
