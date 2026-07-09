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
dbutils.widgets.dropdown("dry_run", "true", ["true", "false"], "Dry run (print only)")
dbutils.widgets.text("catalog", "abac_demo", "Catalog")
DRY_RUN = dbutils.widgets.get("dry_run").lower() == "true"
CATALOG = dbutils.widgets.get("catalog")
CONTROL_TABLE = f"{CATALOG}.governance.policy_control"
MAPPING_TABLE = f"{CATALOG}.governance.rls_user_grants"

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

def render(r, catalog):
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

rows = spark.sql(f"SELECT policy_name, policy_type, udf, attr_types, tag_key, tag_values, "
                 f"to_principals, except_principals, enabled, comment "
                 f"FROM {CONTROL_TABLE} ORDER BY policy_name").collect()
live = {r[0] for r in spark.sql(f"SHOW POLICIES ON CATALOG {CATALOG}").collect()}

# COMMAND ----------
out, warnings = [], []
def log(s=""):
    out.append(s); print(s)

# Decide an action + rendered DDL for every row (no side effects yet).
plan = []   # (name, action, ddl_or_none, precheck_error_or_none)
for r in rows:
    name = r["policy_name"]
    if not r["enabled"]:
        plan.append((name, "DROP" if name in live else "DISABLED", None, None))
        continue
    # principal pre-check
    principals = list(r["to_principals"] or []) + list(r["except_principals"] or [])
    missing = [p for p in principals if not known_principal(p)]
    if missing:
        plan.append((name, "SKIP", None, f"principal(s) not found: {', '.join(missing)}"))
        continue
    # render (may raise -> capture as precheck error)
    try:
        ddl = render(r, CATALOG)
    except Exception as e:
        plan.append((name, "SKIP", None, f"render error: {e}"))
        continue
    # zero-grant warning (row filters only). OR semantics: lockout only if NONE
    # of the policy's attributes has any grant.
    attrs = list(r["attr_types"] or [])
    if r["policy_type"] == "ROW_FILTER" and sum(grant_counts.get(a, 0) for a in attrs) == 0:
        warnings.append(f"{name}: 0 grants for any of {attrs} "
                        f"-> everyone in scope will see 0 rows.")
    plan.append((name, "APPLY", ddl, None))

log(f"Catalog {CATALOG} | {len(rows)} control rows | dry_run={DRY_RUN}")
log(f"APPLY={sum(1 for p in plan if p[1]=='APPLY')} "
    f"DROP={sum(1 for p in plan if p[1]=='DROP')} "
    f"SKIP={sum(1 for p in plan if p[1]=='SKIP')}")
log()
for name, action, ddl, err in plan:
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

failures = []
if DRY_RUN:
    log("\n[dry-run] no changes applied, no status written.")
else:
    for name, action, ddl, err in plan:
        try:
            if action == "APPLY":
                spark.sql(ddl); set_status(name, "APPLIED", None, stamp=True); log(f"applied:  {name}")
            elif action == "DROP":
                spark.sql(f"DROP POLICY {q(name)} ON CATALOG {CATALOG}")
                set_status(name, "DISABLED", None, stamp=True); log(f"dropped:  {name}")
            elif action == "DISABLED":
                set_status(name, "DISABLED", None, stamp=True); log(f"disabled: {name}")
            elif action == "SKIP":
                set_status(name, "SKIPPED", err); failures.append(name); log(f"SKIPPED:  {name} ({err})")
        except Exception as e:
            set_status(name, "FAILED", str(e)); failures.append(name); log(f"FAILED:   {name} ({str(e)[:120]})")
    log("\nReconcile complete." + (f" Problems with: {', '.join(failures)}" if failures else " All healthy."))

# COMMAND ----------
result = "\n".join(out)
if failures:
    # All good rows are applied and every status is written; raise so the run is flagged.
    raise RuntimeError("Generator finished with problems:\n" + result)
dbutils.notebook.exit(result)
