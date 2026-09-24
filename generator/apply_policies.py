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

from policy_engine import duplicate_policy_names, quote_full_name, render_policy

# Matches a service-principal application id (UUID); SPs aren't listed by
# SHOW USERS/GROUPS, so the principal pre-check accepts UUID-shaped principals.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

# Configure via environment or CLI flags. No environment-specific defaults are
# baked in: set DATABRICKS_WAREHOUSE_ID (required), and optionally
# DATABRICKS_CONFIG_PROFILE and ABAC_CATALOG.
WAREHOUSE_DEFAULT = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
PROFILE_DEFAULT = os.environ.get("DATABRICKS_CONFIG_PROFILE", "")
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


def legacy_render(row, catalog):
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
    ap.add_argument("--governance-schema", default="governance")
    ap.add_argument("--reconcile-orphans", action="store_true")
    args = ap.parse_args()
    if not args.profile:
        ap.error("--profile is required (or set DATABRICKS_CONFIG_PROFILE explicitly)")
    if not args.warehouse:
        ap.error("--warehouse is required (or set DATABRICKS_WAREHOUSE_ID explicitly)")
    cat = args.catalog
    control = f"{cat}.{args.governance_schema}.policy_control"
    mapping = f"{cat}.{args.governance_schema}.rls_user_grants"
    inventory_table = f"{cat}.{args.governance_schema}.managed_policy_inventory"

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
    columns = ["policy_id", "policy_name", "policy_type", "scope_type", "scope_name", "udf",
               "attr_types", "tag_key", "tag_values", "to_principals", "except_principals",
               "enabled", "comment", "approval_status", "approved_by", "approved_at",
               "change_request_id", "policy_version"]
    raw_rows = sql(f"SELECT {', '.join(columns)} FROM {control} ORDER BY policy_name")
    rows = [dict(zip(columns, row)) for row in raw_rows]
    for row in rows:
        for key in ("attr_types", "tag_values", "to_principals", "except_principals"):
            row[key] = as_list(row.get(key))
    duplicates = duplicate_policy_names(rows)
    if duplicates:
        raise SqlError(f"duplicate policy_name values: {sorted(duplicates)}")
    inventory = {r[0]: (r[1], r[2]) for r in sql(
        f"SELECT policy_name, scope_type, scope_name FROM {inventory_table} WHERE retired_at IS NULL")}

    plan, warnings = [], []
    for r in rows:
        name, ptype, attrs = r["policy_name"], r["policy_type"], as_list(r["attr_types"])
        enabled = str(r["enabled"]).lower() == "true"
        if not enabled:
            if (r.get("approval_status") != "RETIRED" or not r.get("approved_by")
                    or not r.get("approved_at") or not r.get("change_request_id")):
                plan.append((name, "SKIP", None,
                             "disabled policies require approved RETIRED state and change evidence", r))
                continue
            plan.append((name, "DROP" if name in inventory else "DISABLED", None, None, r))
            continue
        missing = [p for p in as_list(r["to_principals"]) + as_list(r["except_principals"]) if not known_principal(p)]
        if missing:
            plan.append((name, "SKIP", None, f"principal(s) not found: {', '.join(missing)}", r))
            continue
        try:
            ddl = render_policy(r)
        except Exception as e:
            plan.append((name, "SKIP", None, f"render error: {e}", r))
            continue
        # OR semantics: lockout only if NONE of the policy's attributes has any grant.
        if ptype == "ROW_FILTER" and sum(grants.get(a, 0) for a in attrs) == 0:
            warnings.append(f"{name}: 0 grants for any of {attrs} -> everyone in scope sees 0 rows.")
        plan.append((name, "APPLY", ddl, None, r))

    if args.reconcile_orphans:
        desired = {r["policy_name"] for r in rows}
        for name, (scope_type, scope_name) in inventory.items():
            if name not in desired:
                plan.append((name, "DROP_ORPHAN", None, None,
                             {"scope_type": scope_type, "scope_name": scope_name}))

    print(f"Catalog {cat} | {len(rows)} control rows | dry_run={args.dry_run}")
    print(f"APPLY={sum(1 for p in plan if p[1]=='APPLY')} "
          f"DROP={sum(1 for p in plan if p[1]=='DROP')} "
          f"SKIP={sum(1 for p in plan if p[1]=='SKIP')}\n")
    for name, action, ddl, err, policy_row in plan:
        print(f"--- {action} {name}" + (f"  [{err}]" if err else ""))
        if ddl:
            print(ddl + "\n")
    for w in warnings:
        print(f"WARNING  {w}")

    if args.dry_run:
        print("\n[dry-run] no changes applied, no status written.")
        skipped = [name for name, action, _, _, _ in plan if action == "SKIP"]
        if skipped:
            print("Dry-run validation failed for: " + ", ".join(skipped))
            sys.exit(1)
        return

    def set_status(name, status, err=None, stamp=False):
        sets = [f"apply_status='{status}'", f"last_error={sql_str(err)}"]
        if stamp:
            sets.append("last_applied_at=current_timestamp()")
        sql(f"UPDATE {control} SET {', '.join(sets)} WHERE policy_name={sql_str(name)}")

    def merge_inventory(policy_row, ddl):
        import hashlib
        ddl_hash = hashlib.sha256(ddl.encode()).hexdigest()
        sql(f"MERGE INTO {inventory_table} t USING (SELECT {sql_str(policy_row['policy_name'])} policy_name) s "
            f"ON t.policy_name=s.policy_name WHEN MATCHED THEN UPDATE SET scope_type={sql_str(policy_row['scope_type'])}, "
            f"scope_name={sql_str(policy_row['scope_name'])}, policy_id={sql_str(policy_row['policy_id'])}, "
            f"policy_version={policy_row['policy_version']}, ddl_hash={sql_str(ddl_hash)}, last_applied_at=current_timestamp(), retired_at=NULL "
            f"WHEN NOT MATCHED THEN INSERT VALUES ({sql_str(policy_row['policy_name'])}, {sql_str(policy_row['scope_type'])}, "
            f"{sql_str(policy_row['scope_name'])}, {sql_str(policy_row['policy_id'])}, {policy_row['policy_version']}, "
            f"{sql_str(ddl_hash)}, 'control-table-abac', current_timestamp(), current_timestamp(), NULL)")

    failures = []
    for name, action, ddl, err, policy_row in plan:
        try:
            if action == "APPLY":
                sql(ddl); merge_inventory(policy_row, ddl); set_status(name, "APPLIED", None, True); print(f"applied:  {name}")
            elif action in ("DROP", "DROP_ORPHAN"):
                sql(f"DROP POLICY {q(name)} ON {policy_row['scope_type']} {quote_full_name(policy_row['scope_name'])}")
                sql(f"UPDATE {inventory_table} SET retired_at=current_timestamp() WHERE policy_name={sql_str(name)}")
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
