#!/usr/bin/env python3
"""apply_policies.py — Control-table-driven ABAC generator + reconciler (hardened, CLI).

Reads governance.policy_control and renders CREATE OR REPLACE POLICY for every
enabled row, then reconciles (drops disabled). Hardened so one bad row cannot
halt the rest:
  * per-policy isolation  — each apply/drop is caught; failures recorded, not fatal mid-run
  * principal pre-check   — to/except validated against SHOW GROUPS/USERS before DDL
  * zero-grant warning    — a row filter with no mapping rows would lock everyone out
  * status write-back     — apply_status / last_applied_at / last_error record reality

`enabled` = desired intent (human-set). `apply_status` = observed reality (set here).
The mirror of generator/apply_policies_job.py (the deployed notebook). Execution is
driven through the Databricks CLI, so no Python SDK dependency is required.

Usage:
    python apply_policies.py [--dry-run] [--profile P] [--warehouse ID] [--catalog C]
Exit code is non-zero if any policy failed or was skipped.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

# Matches a service-principal application id (UUID); SPs aren't listed by
# SHOW USERS/GROUPS, so the principal pre-check accepts UUID-shaped principals.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Configure via environment or CLI flags. No environment-specific defaults are
# baked in: set DATABRICKS_WAREHOUSE_ID (required), and optionally
# DATABRICKS_CONFIG_PROFILE and ABAC_CATALOG.
WAREHOUSE_DEFAULT = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
PROFILE_DEFAULT = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
CATALOG_DEFAULT = os.environ.get("ABAC_CATALOG", "abac_demo")


class SqlError(Exception):
    pass


def _api(method, path, profile, payload=None):
    args = ["databricks", "api", method, path, "--profile", profile]
    if payload is not None:
        args += ["--json", json.dumps(payload)]
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SqlError(proc.stderr.strip() or proc.stdout.strip())
    return json.loads(proc.stdout)


def run_sql(stmt, profile, warehouse, catalog, poll_seconds=900):
    # Submit, then poll to completion so statements slower than wait_timeout are
    # not misreported as failures (default on_wait_timeout=CONTINUE keeps running).
    resp = _api("post", "/api/2.0/sql/statements", profile,
                {"warehouse_id": warehouse, "catalog": catalog,
                 "wait_timeout": "30s", "statement": stmt})
    st = resp.get("status", {})
    state = st.get("state")
    sid = resp.get("statement_id")
    deadline = time.time() + poll_seconds
    while state in ("PENDING", "RUNNING") and time.time() < deadline:
        time.sleep(3)
        resp = _api("get", f"/api/2.0/sql/statements/{sid}", profile)
        st = resp.get("status", {})
        state = st.get("state")
    if state != "SUCCEEDED":
        raise SqlError(st.get("error", {}).get("message", f"statement ended in state {state}"))
    return (resp.get("result") or {}).get("data_array") or []


def q(p):
    return "`" + p.replace("`", "") + "`"


def lit(s):
    # Escape a value spliced into a single-quoted SQL string literal.
    return str(s).replace("'", "''")


def sql_str(s):
    return "NULL" if s is None else "'" + lit(s)[:900] + "'"


def as_list(v):
    if v is None:
        return []
    return json.loads(v) if isinstance(v, str) else v


def principals_clause(to_list, except_list):
    clause = "TO " + ", ".join(q(p) for p in to_list)
    if except_list:
        clause += "\nEXCEPT " + ", ".join(q(p) for p in except_list)
    return clause


def render(row, catalog):
    (name, ptype, udf, attr_types, tag_key, tag_values,
     to_p, ex_p, enabled, comment) = row[:10]
    attr_types = as_list(attr_types)
    tag_values, to_p, ex_p = as_list(tag_values), as_list(to_p), as_list(ex_p)
    c = (comment or "").replace("'", "''")
    if not tag_values:
        raise ValueError("tag_values is empty")
    if ptype == "ROW_FILTER":
        # One slot per (tag_value, attr_type) pair; length picks single vs multi.
        if len(attr_types) != len(tag_values):
            raise ValueError(f"attr_types/tag_values length mismatch: {attr_types} vs {tag_values}")
        aliases, using = [], []
        for i, (tv, at) in enumerate(zip(tag_values, attr_types)):
            alias = f"c{i}"
            aliases.append(f"hasTagValue('{lit(tag_key)}','{lit(tv)}') AS {alias}")
            using.append(f"'{lit(at)}', {alias}")
        return (f"CREATE OR REPLACE POLICY {q(name)} ON CATALOG {catalog}\n"
                f"COMMENT '{c}'\nROW FILTER {udf}\n{principals_clause(to_p, ex_p)}\n"
                f"FOR TABLES\nMATCH COLUMNS {', '.join(aliases)}\n"
                f"USING COLUMNS ({', '.join(using)})")
    if ptype == "COLUMN_MASK":
        val = tag_values[0]
        return (f"CREATE OR REPLACE POLICY {q(name)} ON CATALOG {catalog}\n"
                f"COMMENT '{c}'\nCOLUMN MASK {udf}\n{principals_clause(to_p, ex_p)}\n"
                f"FOR TABLES\nMATCH COLUMNS hasTagValue('{lit(tag_key)}','{lit(val)}') AS m\nON COLUMN m")
    raise ValueError(f"unknown policy_type {ptype}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--profile", default=PROFILE_DEFAULT)
    ap.add_argument("--warehouse", default=WAREHOUSE_DEFAULT)
    ap.add_argument("--catalog", default=CATALOG_DEFAULT)
    args = ap.parse_args()
    cat = args.catalog
    control = f"{cat}.governance.policy_control"
    mapping = f"{cat}.governance.rls_user_grants"

    def sql(s):
        return run_sql(s, args.profile, args.warehouse, cat)

    valid = {"account users"}
    valid |= {r[0] for r in sql("SHOW GROUPS")}
    valid |= {r[0] for r in sql("SHOW USERS")}
    valid_lower = {v.lower() for v in valid}

    def known_principal(p):
        # Case-insensitive match to a known group/user, or a service-principal UUID
        # (SPs are not returned by SHOW USERS/GROUPS).
        return p.lower() in valid_lower or bool(_UUID_RE.match(p.strip()))

    grants = {r[0]: int(r[1]) for r in
              sql(f"SELECT attribute_type, count(*) FROM {mapping} GROUP BY attribute_type")}
    rows = sql("SELECT policy_name, policy_type, udf, attr_types, tag_key, tag_values, "
               "to_principals, except_principals, enabled, comment "
               f"FROM {control} ORDER BY policy_name")
    live = {r[0] for r in sql(f"SHOW POLICIES ON CATALOG {cat}")}

    plan, warnings = [], []
    for r in rows:
        name, ptype, attrs = r[0], r[1], as_list(r[3])
        enabled = str(r[8]).lower() == "true"
        if not enabled:
            plan.append((name, "DROP" if name in live else "DISABLED", None, None))
            continue
        missing = [p for p in as_list(r[6]) + as_list(r[7]) if not known_principal(p)]
        if missing:
            plan.append((name, "SKIP", None, f"principal(s) not found: {', '.join(missing)}"))
            continue
        try:
            ddl = render(r, cat)
        except Exception as e:
            plan.append((name, "SKIP", None, f"render error: {e}"))
            continue
        # OR semantics: lockout only if NONE of the policy's attributes has any grant.
        if ptype == "ROW_FILTER" and sum(grants.get(a, 0) for a in attrs) == 0:
            warnings.append(f"{name}: 0 grants for any of {attrs} -> everyone in scope sees 0 rows.")
        plan.append((name, "APPLY", ddl, None))

    print(f"Catalog {cat} | {len(rows)} control rows | dry_run={args.dry_run}")
    print(f"APPLY={sum(1 for p in plan if p[1]=='APPLY')} "
          f"DROP={sum(1 for p in plan if p[1]=='DROP')} "
          f"SKIP={sum(1 for p in plan if p[1]=='SKIP')}\n")
    for name, action, ddl, err in plan:
        print(f"--- {action} {name}" + (f"  [{err}]" if err else ""))
        if ddl:
            print(ddl + "\n")
    for w in warnings:
        print(f"WARNING  {w}")

    if args.dry_run:
        print("\n[dry-run] no changes applied, no status written.")
        return

    def set_status(name, status, err=None, stamp=False):
        sets = [f"apply_status='{status}'", f"last_error={sql_str(err)}"]
        if stamp:
            sets.append("last_applied_at=current_timestamp()")
        sql(f"UPDATE {control} SET {', '.join(sets)} WHERE policy_name={sql_str(name)}")

    failures = []
    for name, action, ddl, err in plan:
        try:
            if action == "APPLY":
                sql(ddl); set_status(name, "APPLIED", None, True); print(f"applied:  {name}")
            elif action == "DROP":
                sql(f"DROP POLICY {q(name)} ON CATALOG {cat}")
                set_status(name, "DISABLED", None, True); print(f"dropped:  {name}")
            elif action == "DISABLED":
                set_status(name, "DISABLED", None, True); print(f"disabled: {name}")
            elif action == "SKIP":
                set_status(name, "SKIPPED", err); failures.append(name); print(f"SKIPPED:  {name}")
        except SqlError as e:
            set_status(name, "FAILED", str(e)); failures.append(name)
            print(f"FAILED:   {name} ({str(e)[:120]})")

    if failures:
        print(f"\nDone with problems: {', '.join(failures)}")
        sys.exit(1)
    print("\nReconcile complete. All healthy.")


if __name__ == "__main__":
    main()
