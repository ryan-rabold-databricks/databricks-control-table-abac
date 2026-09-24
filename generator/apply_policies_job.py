# Databricks notebook source
# MAGIC %md
# MAGIC # ABAC Policy Generator (job version, hardened)
# MAGIC Renders `CREATE OR REPLACE POLICY` from `governance.policy_control` and, when
# MAGIC `dry_run=false`, applies + reconciles. Hardened so one bad row can't halt the rest:
# MAGIC - **per-policy isolation** — each apply/drop is caught; failures are recorded, not fatal mid-run
# MAGIC - **principal pre-check** — `to`/`except` validated against SHOW GROUPS/USERS before DDL
# MAGIC - **zero-grant warning** — a row filter whose attribute has no mapping rows would lock everyone out
# MAGIC - **status write-back** — `apply_status`/`last_applied_at`/`last_error` record observed reality
# MAGIC
# MAGIC `enabled` stays *desired intent* (human-set). `apply_status` is *observed reality* (set here).
# MAGIC The audit views read `apply_status`, so they never show a policy that didn't actually apply.

# COMMAND ----------
from policy_engine import duplicate_policy_names, quote_full_name, render_policy

# COMMAND ----------
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry run (print only)")
dbutils.widgets.text("catalog", "abac_demo", "Catalog")
dbutils.widgets.text("governance_schema", "governance", "Governance schema")
dbutils.widgets.dropdown("reconcile_orphans", "false", ["true", "false"], "Retire managed orphans")
dbutils.widgets.text("execution_id", "interactive", "Job run ID")
DRY_RUN = dbutils.widgets.get("dry_run").lower() == "true"
CATALOG = dbutils.widgets.get("catalog")
GOVERNANCE_SCHEMA = dbutils.widgets.get("governance_schema")
RECONCILE_ORPHANS = dbutils.widgets.get("reconcile_orphans").lower() == "true"
EXECUTION_ID = dbutils.widgets.get("execution_id") or "interactive"
CONTROL_TABLE = f"{CATALOG}.{GOVERNANCE_SCHEMA}.policy_control"
MAPPING_TABLE = f"{CATALOG}.{GOVERNANCE_SCHEMA}.rls_principal_grants"
INVENTORY_TABLE = f"{CATALOG}.{GOVERNANCE_SCHEMA}.managed_policy_inventory"
EVENT_TABLE = f"{CATALOG}.{GOVERNANCE_SCHEMA}.policy_deployment_events"

# COMMAND ----------
import re

# Matches a service-principal application id (UUID); SPs aren't listed by
# SHOW USERS/GROUPS, so the principal pre-check accepts UUID-shaped principals.
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)

def q(p):
    return "`" + p.replace("`", "") + "`"

def lit(s):
    # Escape a value spliced into a single-quoted SQL string literal.
    return str(s).replace("'", "''")

def principals_clause(to_list, except_list):
    clause = "TO " + ", ".join(q(p) for p in to_list)
    if except_list:
        clause += "\nEXCEPT " + ", ".join(q(p) for p in except_list)
    return clause

def legacy_render(r, catalog):
    name, ptype, udf = r["policy_name"], r["policy_type"], r["udf"]
    attr_types = list(r["attr_types"] or [])
    tag_key = r["tag_key"]
    tag_values = list(r["tag_values"] or [])
    to_p = list(r["to_principals"] or [])
    ex_p = list(r["except_principals"] or [])
    c = (r["comment"] or "").replace("'", "''")
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

def sql_str(s):
    return "NULL" if s is None else "'" + str(s).replace("'", "''")[:900] + "'"

# COMMAND ----------
# Ground truth we validate against.
valid_principals = {"account users"}
valid_principals |= {r[0] for r in spark.sql("SHOW GROUPS").collect()}
valid_principals |= {r[0] for r in spark.sql("SHOW USERS").collect()}
_valid_lower = {v.lower() for v in valid_principals}

def known_principal(p):
    # Case-insensitive match to a known group/user, or a service-principal UUID.
    return p.lower() in _valid_lower or bool(_UUID_RE.match(p.strip()))

grant_counts = {r["attribute_type"]: r["c"] for r in spark.sql(
    f"SELECT attribute_type, count(*) c FROM {MAPPING_TABLE} GROUP BY attribute_type").collect()}

rows = spark.sql(f"SELECT policy_id, policy_name, policy_type, scope_type, scope_name, udf, attr_types, tag_key, tag_values, "
                 f"to_principals, except_principals, enabled, comment, approval_status, approved_by, approved_at, "
                 f"change_request_id, policy_version "
                 f"FROM {CONTROL_TABLE} ORDER BY policy_name").collect()
row_dicts = [r.asDict(recursive=True) for r in rows]
duplicate_names = duplicate_policy_names(row_dicts)
if duplicate_names:
    raise ValueError(f"Duplicate policy_name values: {sorted(duplicate_names)}")
duplicate_ids = [r[0] for r in spark.sql(
    f"SELECT policy_id FROM {CONTROL_TABLE} GROUP BY policy_id HAVING count(*) > 1"
).collect()]
if duplicate_ids:
    raise ValueError(f"Duplicate policy_id values: {sorted(duplicate_ids)}")
duplicate_grants = spark.sql(
    f"SELECT principal_type, coalesce(principal_id, principal_name) principal_key, attribute_type, attribute_value, count(*) c FROM {MAPPING_TABLE} "
    "WHERE revoked_at IS NULL AND effective_date <= current_date() "
    "AND (expiration_date IS NULL OR expiration_date >= current_date()) "
    "GROUP BY principal_type, coalesce(principal_id, principal_name), attribute_type, attribute_value HAVING count(*) > 1"
).limit(20).collect()
if duplicate_grants:
    raise ValueError(f"Duplicate active row-scope grants found: {duplicate_grants}")
inventory = {r[0]: (r[1], r[2]) for r in spark.sql(
    f"SELECT policy_name, scope_type, scope_name FROM {INVENTORY_TABLE} WHERE retired_at IS NULL"
).collect()}

# COMMAND ----------
out, warnings = [], []
def log(s=""):
    out.append(s); print(s)

# Decide an action + rendered DDL for every row (no side effects yet).
plan = []   # (name, action, ddl_or_none, precheck_error_or_none)
for r in row_dicts:
    name = r["policy_name"]
    if not r["enabled"]:
        if r.get("approval_status") != "RETIRED" or not r.get("approved_by") or not r.get("approved_at") or not r.get("change_request_id"):
            plan.append((name, "SKIP", None, "disabled policies require approved RETIRED state and change evidence", r))
            continue
        plan.append((name, "DROP" if name in inventory else "DISABLED", None, None, r))
        continue
    # principal pre-check
    principals = list(r["to_principals"] or []) + list(r["except_principals"] or [])
    missing = [p for p in principals if not known_principal(p)]
    if missing:
        plan.append((name, "SKIP", None, f"principal(s) not found: {', '.join(missing)}", r))
        continue
    # render (may raise -> capture as precheck error)
    try:
        spark.sql(f"DESCRIBE FUNCTION EXTENDED {quote_full_name(r['udf'])}").collect()
        ddl = render_policy(r)
    except Exception as e:
        plan.append((name, "SKIP", None, f"render error: {e}", r))
        continue
    # zero-grant warning (row filters only). OR semantics: lockout only if NONE
    # of the policy's attributes has any grant.
    attrs = list(r["attr_types"] or [])
    if r["policy_type"] == "ROW_FILTER" and sum(grant_counts.get(a, 0) for a in attrs) == 0:
        warnings.append(f"{name}: 0 grants for any of {attrs} "
                        f"-> everyone in scope will see 0 rows.")
    plan.append((name, "APPLY", ddl, None, r))

if RECONCILE_ORPHANS:
    desired_names = {r["policy_name"] for r in row_dicts}
    for name, (scope_type, scope_name) in inventory.items():
        if name not in desired_names:
            orphan = {"scope_type": scope_type, "scope_name": scope_name, "policy_version": None}
            plan.append((name, "DROP_ORPHAN", None, None, orphan))

log(f"Catalog {CATALOG} | {len(rows)} control rows | dry_run={DRY_RUN}")
log(f"APPLY={sum(1 for p in plan if p[1]=='APPLY')} "
    f"DROP={sum(1 for p in plan if p[1]=='DROP')} "
    f"SKIP={sum(1 for p in plan if p[1]=='SKIP')}")
log()
for name, action, ddl, err, policy_row in plan:
    log("=" * 78); log(f"-- {action}  {name}" + (f"   [{err}]" if err else "")); log("=" * 78)
    if ddl:
        log(ddl)
    log()
for w in warnings:
    log(f"WARNING  {w}")

# COMMAND ----------
def set_status(name, status, err=None, stamp=False):
    sets = [f"apply_status='{status}'", f"last_error={sql_str(err)}"]
    if stamp:
        sets.append("last_applied_at=current_timestamp()")
    spark.sql(f"UPDATE {CONTROL_TABLE} SET {', '.join(sets)} "
              f"WHERE policy_name={sql_str(name)}")

def record_event(name, version, action, outcome, ddl=None, error=None):
    ddl_hash = None if ddl is None else __import__("hashlib").sha256(ddl.encode()).hexdigest()
    spark.sql(f"INSERT INTO {EVENT_TABLE} SELECT uuid(), {sql_str(EXECUTION_ID)}, "
              f"{sql_str(name)}, {version if version is not None else 'NULL'}, {sql_str(action)}, {sql_str(outcome)}, "
              f"{sql_str(ddl_hash)}, current_user(), current_timestamp(), {sql_str(error)}")

def merge_inventory(policy_row, ddl):
    ddl_hash = __import__("hashlib").sha256(ddl.encode()).hexdigest()
    spark.sql(f"MERGE INTO {INVENTORY_TABLE} t USING (SELECT {sql_str(policy_row['policy_name'])} policy_name) s "
              f"ON t.policy_name=s.policy_name WHEN MATCHED THEN UPDATE SET scope_type={sql_str(policy_row['scope_type'])}, "
              f"scope_name={sql_str(policy_row['scope_name'])}, policy_id={sql_str(policy_row['policy_id'])}, "
              f"policy_version={policy_row['policy_version']}, ddl_hash={sql_str(ddl_hash)}, last_applied_at=current_timestamp(), retired_at=NULL "
              f"WHEN NOT MATCHED THEN INSERT (policy_name, scope_type, scope_name, policy_id, policy_version, ddl_hash, "
              f"managed_by, first_applied_at, last_applied_at, retired_at) VALUES "
              f"({sql_str(policy_row['policy_name'])}, {sql_str(policy_row['scope_type'])}, "
              f"{sql_str(policy_row['scope_name'])}, {sql_str(policy_row['policy_id'])}, {policy_row['policy_version']}, "
              f"{sql_str(ddl_hash)}, 'control-table-abac', current_timestamp(), current_timestamp(), NULL)")

failures = []
if DRY_RUN:
    log("\n[dry-run] no changes applied, no status written.")
    skipped = [name for name, action, _, _, _ in plan if action == "SKIP"]
    if skipped:
        raise RuntimeError("Dry-run validation failed for: " + ", ".join(skipped))
else:
    for name, action, ddl, err, policy_row in plan:
        try:
            if action == "APPLY":
                spark.sql(ddl); merge_inventory(policy_row, ddl); set_status(name, "APPLIED", None, stamp=True)
                record_event(name, policy_row.get("policy_version"), action, "SUCCEEDED", ddl); log(f"applied:  {name}")
            elif action in ("DROP", "DROP_ORPHAN"):
                spark.sql(f"DROP POLICY {q(name)} ON {policy_row['scope_type']} {quote_full_name(policy_row['scope_name'])}")
                spark.sql(f"UPDATE {INVENTORY_TABLE} SET retired_at=current_timestamp() WHERE policy_name={sql_str(name)}")
                set_status(name, "DISABLED", None, stamp=True); log(f"dropped:  {name}")
            elif action == "DISABLED":
                set_status(name, "DISABLED", None, stamp=True); log(f"disabled: {name}")
            elif action == "SKIP":
                set_status(name, "SKIPPED", err); failures.append(name); log(f"SKIPPED:  {name} ({err})")
        except Exception as e:
            set_status(name, "FAILED", str(e)); failures.append(name); log(f"FAILED:   {name} ({str(e)[:120]})")
            try:
                record_event(name, policy_row.get("policy_version"), action, "FAILED", ddl, str(e))
            except Exception as audit_error:
                # Preserve the original apply error; audit failure is still visible in job output.
                log(f"AUDIT FAILED: {name} ({str(audit_error)[:120]})")
    log("\nReconcile complete." + (f" Problems with: {', '.join(failures)}" if failures else " All healthy."))

# COMMAND ----------
result = "\n".join(out)
if failures:
    # All good rows are applied and every status is written; raise so the run is flagged.
    raise RuntimeError("Generator finished with problems:\n" + result)

# Confirm actual table-level resolution, not only generator-written status.
if not DRY_RUN:
    import time
    expected = spark.sql(
        f"SELECT policy_name, schema_name, table_name FROM {CATALOG}.{GOVERNANCE_SCHEMA}.vw_policy_audit"
    ).collect()
    unresolved = []
    for item in expected:
        table_name = quote_full_name(f"{CATALOG}.{item['schema_name']}.{item['table_name']}")
        found = False
        for attempt in range(6):
            effective = spark.sql(f"SHOW EFFECTIVE POLICIES ON TABLE {table_name}").collect()
            if item["policy_name"] in "\n".join(str(row) for row in effective):
                found = True
                break
            time.sleep(10)
        if not found:
            unresolved.append(f"{item['policy_name']} on {table_name}")
    if unresolved:
        raise RuntimeError("Policies applied but not observed by SHOW EFFECTIVE POLICIES: " + ", ".join(unresolved))
dbutils.notebook.exit(result)
