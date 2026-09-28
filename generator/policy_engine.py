"""Shared, side-effect-free validation and DDL rendering for the ABAC framework."""

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
import re

ALLOWED_POLICY_TYPES = {"ROW_FILTER", "COLUMN_MASK"}
ALLOWED_SCOPE_TYPES = {"CATALOG", "SCHEMA", "TABLE"}
APPROVED_STATE = "APPROVED"
MAX_MATCH_COLUMNS = 3
_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class PolicyValidationError(ValueError):
    pass


def quote_identifier(value: str) -> str:
    if not value or "`" in value:
        raise PolicyValidationError(f"unsafe or empty identifier: {value!r}")
    return f"`{value}`"


def quote_full_name(value: str) -> str:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not p or "`" in p for p in parts):
        raise PolicyValidationError(f"invalid UC full name: {value!r}")
    return ".".join(quote_identifier(p) for p in parts)


def literal(value: Any) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _list(row: Mapping[str, Any], key: str) -> list:
    value = row.get(key)
    return list(value or [])


def validate_policy(row: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    name = row.get("policy_name")
    ptype = str(row.get("policy_type") or "").upper()
    scope_type = str(row.get("scope_type") or "CATALOG").upper()
    scope_name = row.get("scope_name")
    tags = _list(row, "tag_values")
    attrs = _list(row, "attr_types")
    to_principals = _list(row, "to_principals")

    if not name or not _IDENTIFIER.fullmatch(str(name)):
        errors.append("policy_name must match [A-Za-z_][A-Za-z0-9_]*")
    if ptype not in ALLOWED_POLICY_TYPES:
        errors.append(f"policy_type must be one of {sorted(ALLOWED_POLICY_TYPES)}")
    if scope_type not in ALLOWED_SCOPE_TYPES:
        errors.append(f"scope_type must be one of {sorted(ALLOWED_SCOPE_TYPES)}")
    if not scope_name:
        errors.append("scope_name is required")
    else:
        try:
            quote_full_name(str(scope_name))
        except PolicyValidationError as exc:
            errors.append(str(exc))
    if not row.get("udf"):
        errors.append("udf is required")
    else:
        try:
            quote_full_name(str(row["udf"]))
        except PolicyValidationError as exc:
            errors.append(str(exc))
    if not row.get("tag_key") or not tags:
        errors.append("tag_key and at least one tag_value are required")
    if not to_principals:
        errors.append("at least one TO principal is required")
    if len(to_principals) + len(_list(row, "except_principals")) > 20:
        errors.append("TO + EXCEPT principals exceed the Databricks limit of 20")
    if ptype == "ROW_FILTER":
        if len(attrs) != len(tags):
            errors.append("attr_types and tag_values must have equal lengths")
        if len(attrs) > MAX_MATCH_COLUMNS:
            errors.append(f"row filters support at most {MAX_MATCH_COLUMNS} MATCH COLUMNS expressions")
    if ptype == "COLUMN_MASK" and len(tags) != 1:
        errors.append("column-mask policies require exactly one tag_value")
    if row.get("approval_status") != APPROVED_STATE:
        errors.append("approval_status must be APPROVED")
    if not row.get("approved_by") or not row.get("approved_at"):
        errors.append("approved_by and approved_at are required")
    if not row.get("change_request_id"):
        errors.append("change_request_id is required")
    version = row.get("policy_version")
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors.append("policy_version must be a positive integer")
    return errors


def render_policy(row: Mapping[str, Any]) -> str:
    errors = validate_policy(row)
    if errors:
        raise PolicyValidationError("; ".join(errors))

    name = quote_identifier(str(row["policy_name"]))
    ptype = str(row["policy_type"]).upper()
    scope_type = str(row.get("scope_type") or "CATALOG").upper()
    scope_name = quote_full_name(str(row["scope_name"]))
    udf = quote_full_name(str(row["udf"]))
    tag_key = str(row["tag_key"])
    tag_values = _list(row, "tag_values")
    attrs = _list(row, "attr_types")
    to_clause = ", ".join(quote_identifier(str(p)) for p in _list(row, "to_principals"))
    excepts = _list(row, "except_principals")
    principal_clause = f"TO {to_clause}"
    if excepts:
        principal_clause += "\nEXCEPT " + ", ".join(quote_identifier(str(p)) for p in excepts)
    comment = literal(row.get("comment") or "")
    header = (
        f"CREATE OR REPLACE POLICY {name} ON {scope_type} {scope_name}\n"
        f"COMMENT {comment}\n"
    )
    if ptype == "ROW_FILTER":
        matches, using = [], []
        for index, (tag_value, attr_type) in enumerate(zip(tag_values, attrs)):
            alias = f"c{index}"
            matches.append(
                f"has_tag_value({literal(tag_key)}, {literal(tag_value)}) AS {alias}"
            )
            using.extend([literal(attr_type), alias])
        return (
            header
            + f"ROW FILTER {udf}\n{principal_clause}\nFOR TABLES\n"
            + "MATCH COLUMNS " + ", ".join(matches) + "\n"
            + "USING COLUMNS (" + ", ".join(using) + ")"
        )
    alias = "masked_column"
    return (
        header
        + f"COLUMN MASK {udf}\n{principal_clause}\nFOR TABLES\n"
        + f"MATCH COLUMNS has_tag_value({literal(tag_key)}, {literal(tag_values[0])}) AS {alias}\n"
        + f"ON COLUMN {alias}"
    )


def duplicate_policy_names(rows: Iterable[Mapping[str, Any]]) -> set[str]:
    seen, duplicates = set(), set()
    for row in rows:
        name = str(row.get("policy_name"))
        if name in seen:
            duplicates.add(name)
        seen.add(name)
    return duplicates


def effective_policy_names(rows: Iterable[Any]) -> set[str]:
    """Extract exact policy names from SHOW EFFECTIVE POLICIES result rows."""
    names: set[str] = set()
    for row in rows:
        values = row.asDict(recursive=True) if hasattr(row, "asDict") else row
        if isinstance(values, Mapping):
            normalized = {str(key).strip().lower().replace("_", " "): value
                          for key, value in values.items()}
            value = normalized.get("policy name")
            if value is not None:
                names.add(str(value))
    return names
