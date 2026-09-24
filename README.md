# Control-Table-Driven ABAC on Databricks

A reference implementation of a governance pattern for Unity Catalog: **one declarative
control table + column tags drive every row-filter and column-mask policy**, and a
generator (runnable as a CLI or a Databricks job) renders native UC ABAC policies from
it. Adding a policy is "insert a row + tag the columns + run the generator." Access —
and policy health — is auditable from three views.

> ## ⚠️ Disclaimer
>
> This project is provided **for demonstration and educational purposes only**. It is **not an
> officially supported Databricks product or solution**, carries **no warranty or support** (express
> or implied), and is licensed **as-is** under Apache-2.0. It is not covered by any Databricks Service
> Level Agreement or support agreement. Review, harden, security-test, and adapt it to your own
> standards before any production use. You are solely responsible for any deployment and its costs.

See [Configuration](#configuration) to point it at your workspace, warehouse, and catalog.

---

## The core idea (and the one thing that is NOT feasible)

The instinct is "a control table the runtime reads to decide access." That is **not**
how UC ABAC works, and building it that way fights the platform:

- A row-filter / column-mask UDF is a **SQL function and cannot do dynamic SQL** — it
  can't be handed a table name at query time.
- A policy **cannot pass the matched tag value** into its UDF.

So the control table is the **source of truth a generator renders policies from at
deploy time** — not a runtime lookup. The generated artifacts are real, native UC
policies, which keeps UC's own auditability intact. The per-user, per-row scoping *is*
pure data (`rls_principal_grants`), read at query time by one standardized function.

**Two states, kept separate:** `policy_control.enabled` is *desired intent* (human-set);
`apply_status` is *observed reality* (generator-set). The audit views read `apply_status`,
so they never show a policy that failed to apply or was dropped.

---

## Architecture

```
  governance.policy_control      ← 1 row  = 1 policy   (declarative source of truth)
  governance.rls_principal_grants ← 1 row = 1 principal grant (who is scoped to what) [row-filter only]
            │
            ▼
  apply_policies (CLI or Databricks job)   ← renders CREATE OR REPLACE POLICY,
            │                                 reconciles, writes back apply_status
            ▼
  Native UC ABAC policies  ──bind──►  governance.rls_scope_filter(attr_type, value)
                                      governance.rls_scope_filter2(a1_type,a1,a2_type,a2)  [OR]
                                      governance.mask_value_any(value)
            │
   activated by governed-tag assignments on columns
            │
            ▼
  governance.vw_policy_audit          ← which APPLIED policy hits which column + principals
  governance.vw_effective_row_access  ← who can see which rows
  governance.vw_policy_health         ← desired vs observed, grant coverage, lockout signal
```

## Objects (all in `<your-catalog>.governance`)

| Object | Role |
|---|---|
| `policy_control` (table) | One row per policy. The "insert one row" surface. `enabled`=intent, `apply_status`=reality. |
| `rls_principal_grants` (table) | Unified tall mapping `(USER|GROUP|SERVICE_PRINCIPAL, identity) → (attribute_type, attribute_value)`. |
| `rls_scope_filter(attr_type, value)` | Single-attribute row filter. |
| `rls_scope_filter2(a1_type,a1,a2_type,a2)` | Two-attribute **OR** row filter. |
| `mask_value_any(value)` | Universal type-aware redactor for all masks. |
| `vw_policy_audit` | Applied policy ↔ tagged column ↔ principals. |
| `vw_effective_row_access` | Principal ↔ attribute values they can see. |
| `vw_policy_health` | `OK` / `NOT_IN_EFFECT` / `PENDING_DISABLE` / `LOCKOUT_NO_GRANTS`. |

Live policies: `mask_pii` (mask), `rls_employee` (single-attribute), `rls_encounter`
(multi-attribute OR). Governed tags: `row_filter_policy` (row filters) and
`masking_rule` (masks) — both registered via the Tag Policy API.

Demo data: `gold.fact_encounter` (300 rows), `gold.dim_patient` (50 rows, PII),
`healthcare_demo.employee_compensation` (10 rows), `gold.provider_productivity` (30 rows,
used by the add-a-policy runbook).

---

## Single vs. multi-attribute row filters

A row-filter control row carries two aligned arrays: `tag_values` (what activates it on
a column) and `attr_types` (the mapping key passed to the function). **Array length picks
the binding:**

| | `attr_types` / `tag_values` | UDF |
|---|---|---|
| Single-attribute (`rls_employee`) | 1 element | `rls_scope_filter` |
| Multi-attribute OR (`rls_encounter`) | 2 elements | `rls_scope_filter2` |

**tag_values are decoupled from attr_types** so overlapping attributes don't collide.
`rls_encounter` uses tag_values `['enc_department','enc_provider']` but attr_types
`['department_id','provider_id']` — a standalone provider policy keyed on the raw
`provider_id` tag would otherwise also match `fact_encounter` and trip
`UC_ABAC_MULTIPLE_ROW_FILTERS`.

---

## Proven behavior (live)

| Scenario | Result |
|---|---|
| `fact_encounter`, granted department CARDIOLOGY **and** provider DR004 | sees all CARDIOLOGY rows **OR** any DR004 rows (96 of 300) — the union |
| `employee_compensation` as a mid-level manager | sees own row + entire reporting subtree (6 of 10) |
| `dim_patient` PII, non-privileged | `ssn`/`email` → `***REDACTED***`, DOB → `1900-01-01` |
| Row filter live, zero grants | user sees 0 rows; `vw_policy_health` = `LOCKOUT_NO_GRANTS` |
| Grant added to `rls_principal_grants` | takes effect immediately — **no policy edit, no redeploy** |

Recursive `rls_employee`: the reporting tree's transitive closure is pre-expanded into
`rls_principal_grants` by a materialization step, so the same equality function handles it.

---

## The generator is hardened

- **Per-policy isolation** — each apply/drop is caught; one bad row is recorded, the rest still process.
- **Principal pre-check** — `to`/`except` validated against `SHOW GROUPS`/`SHOW USERS` (+`account users`) before DDL; a missing principal → `SKIPPED`, not a fatal `PRINCIPAL_DOES_NOT_EXIST`.
- **Zero-grant warning** — a row filter with no grants for any of its attributes would lock everyone out.
- **Status write-back** — `apply_status` / `last_applied_at` / `last_error` after every run; the notebook raises at the end if anything failed so the job run is flagged.

The CLI and deployed notebook share validation and DDL rendering from
`generator/policy_engine.py`. The Lakeflow Job is deployed with the bundle resource
`resources/abac_apply_policies.job.yml`, defaults to `dry_run=true`, and enforces a single
concurrent run.

For production controls, upgrade guidance, approval gates, managed-orphan reconciliation,
and deployment instructions, see [`PRODUCTION_DEPLOYMENT.md`](PRODUCTION_DEPLOYMENT.md).
For the target FEVM workspace redeployment sequence, see
[`REDEPLOY_FEV_CLASSIC_STABLE.md`](REDEPLOY_FEV_CLASSIC_STABLE.md).

---

## Runtime constraints discovered (these matter for rollout)

1. **ABAC requires GOVERNED tags** — a policy condition only accepts a tag key
   registered via the Tag Policy API, with the value in its allowed set. Plain
   `ALTER TABLE … SET TAGS` keys don't work in policies.
2. **A UDF can't receive the matched tag value** — the attribute type is passed as a
   literal in `USING COLUMNS ('department_id', col)`.
3. **At most ONE row filter per table** (`UC_ABAC_MULTIPLE_ROW_FILTERS`). Contradicts
   a common assumption that row filters can be stacked. Multiple attributes on one table must
   be combined into a single function — that's what `rls_scope_filter2` is for.
4. **`information_schema.abac_policy_definitions` exists but isn't queryable** on this
   runtime, so the audit views rely on generator-written `apply_status` rather than
   live policy truth. Revisit when it GAs.

---

## How to add a policy

**New single-attribute row filter** (worked example in `DEMO_add_provider_filter.md`):
1. Register the governed tag value (`databricks tag-policies update-tag-policy …`).
2. `INSERT` one row into `policy_control` (`attr_types=array('x')`, `tag_values=array('x')`, udf `rls_scope_filter`).
3. Tag the column: `ALTER COLUMN x SET TAGS ('row_filter_policy'='x')`.
4. Run the generator (job or CLI).
5. Grant users, groups, or service principals with `INSERT`s into `rls_principal_grants`.

**Add a second attribute to a table** (one table needs A **or** B): use one combined row
(`attr_types=array('a','b')`, udf `rls_scope_filter2`, policy-scoped `tag_values`),
**replacing** any single-attribute policy on that table — do not stack two.

**New masked column:** just tag it — `ALTER COLUMN x SET TAGS ('masking_rule'='redact')`. No new policy.

**Retire a policy:** set `enabled=false` and re-run the generator (it drops it and marks it `DISABLED`).

---

## Configuration

The scripts bake in **no** environment-specific values. Set these (or pass the
equivalent `--warehouse` / `--profile` / `--catalog` flags):

| Variable | Purpose | Default |
|---|---|---|
| `DATABRICKS_WAREHOUSE_ID` | SQL warehouse the CLI generator / `run_sql.py` use | *(required)* |
| `DATABRICKS_CONFIG_PROFILE` | Explicit Databricks CLI auth profile | *(required unless `--profile` is passed)* |
| `ABAC_CATALOG` | Target Unity Catalog (**must already exist**) | `abac_demo` |

- **Catalog is an input parameter, not created here.** The SQL files use a `{{catalog}}`
  placeholder that `run_sql.py` substitutes from `--catalog` / `ABAC_CATALOG`. Point it at any
  existing catalog you have `CREATE` on; `sql/00_setup.sql` then creates the schemas + demo tables
  inside it. (For the copy-paste runbook, replace `{{catalog}}` by hand.)
- **Governed tags** are account/metastore-scoped and **must be registered first** (before `02`,
  which tags columns). See the prerequisite block at the top of `sql/00_setup.sql` for the exact
  `databricks tag-policies` commands: `row_filter_policy` (values `enc_department`, `enc_provider`,
  `employee_id`, `provider_id`) and `masking_rule` (value `redact`).
- **Principals** in `03` (`account users`, `data-governance-team`) must exist in your workspace, or
  the generator's pre-check SKIPs the policy. Change them to your groups.
- In `03`/`05`, `you@example.com` is a placeholder — replace the `E002` manager node and the grant
  rows with your own login to see the filters act on your session.

---

## Run order (fresh install)

First register the governed tags (see `sql/00_setup.sql` header), then:

```bash
export DATABRICKS_WAREHOUSE_ID=<your-warehouse-id>
export ABAC_CATALOG=<your-existing-catalog>
export DATABRICKS_CONFIG_PROFILE=<your-explicit-profile>
python generator/run_sql.py sql/00_setup.sql             # schemas + demo tables in your catalog
python generator/run_sql.py sql/01_foundation.sql        # functions + control tables
python generator/run_sql.py sql/02_data.sql              # synthetic data + column tags
python generator/run_sql.py sql/03_control_data.sql      # seed control rows + grants
python generator/run_sql.py sql/04_audit_views.sql       # audit + health views
python generator/run_sql.py sql/05_employee_hierarchy.sql# recursive employee filter data
python generator/apply_policies.py                       # render + apply + reconcile
```

Final live policies: `mask_pii`, `rls_employee` (single-attribute), `rls_encounter`
(department-OR-provider). `sql/07_rename.sql` is the legacy **one-time migration** from
old `rbac_*` objects. Existing `rls_user_grants` deployments should instead run
`sql/migrations/002_principal_grants.sql`, refresh foundation/views, validate, and then run
`sql/migrations/003_drop_legacy_user_grants.sql`.
— not part of a fresh install.

---

## Mapping to a shape-based policy estate

| Policy shape | This demo |
|---|---|
| A. identity row filter (`rls_employee`, `rls_provider`, …) | one `rls_scope_filter` + `rls_principal_grants` + N control rows |
| A. multiple attributes on one table (roadmap #9∩#10∩#11) | one `rls_scope_filter2` (OR) + one combined control row |
| B. static column mask (`mask_sensitive_hr`, `mask_phi`) | one `mask_value_any` + `masking_rule` tag |
| C. group-membership gate (`rbac_supply_chain_subdomain`) | control row with a group in `to`/`except` |
| D. hide-all (`sensitivity_legal`) | control row: `TO account users EXCEPT <allowed group>` |

The shape-A policies collapse to **one function family + one control row each** — the
consolidation this pattern enables, with the governed-tag, single-row-filter, and
tag/attr decoupling constraints handled correctly.
