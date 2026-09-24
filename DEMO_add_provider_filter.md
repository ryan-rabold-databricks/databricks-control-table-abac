# Demo: add a row-filter policy live — `rls_provider`

Audience: governance stakeholders. Story: *"To scope a new table by a new
attribute, the governance team adds one row and tags a column — no bespoke code."*

Workspace: `https://YOUR-WORKSPACE-HOST.cloud.databricks.com`
Table on show: `{{catalog}}.gold.provider_productivity` (10 providers x 3 months = 30 rows).

> Replace `{{catalog}}` with your Unity Catalog name in each statement before running.

Starting state (already reset): no `rls_provider` policy, column untagged, no `provider_id`
grants, and `provider_id` is NOT yet a governed tag value.

---

## 0. Baseline — "today everyone sees everything"
SQL Editor:
```sql
SELECT provider_id, count(*) FROM {{catalog}}.gold.provider_productivity
GROUP BY 1 ORDER BY 1;
```
> 10 providers, all visible. Say: *"No row filter yet — every analyst sees every provider's numbers."*

## 1. Register the attribute in the governance vocabulary
Governed tags are a closed set, so the value must exist before it can be used.
**UI:** Catalog → Governed tags → `row_filter_policy` → Edit → add value `provider_id`.
**or CLI:**
```bash
databricks tag-policies update-tag-policy row_filter_policy values --profile <PROFILE> --json \
'{"tag_key":"row_filter_policy","values":[{"name":"department"},{"name":"data_classification"},{"name":"department_id"},{"name":"facility_id"},{"name":"employee_id"},{"name":"provider_id"}]}'
```

## 2. Declare the policy — ONE row in the control table
SQL Editor:
```sql
INSERT INTO {{catalog}}.governance.policy_control
  (policy_id, policy_name, policy_type, scope_type, scope_name, udf, attr_types, tag_key, tag_values,
   to_principals, except_principals, enabled, comment, owner, approval_status,
   approved_by, approved_at, change_request_id, policy_version, created_by, created_at, updated_at)
VALUES (
  'POL-PROVIDER-001', 'rls_provider', 'ROW_FILTER', 'CATALOG', '{{catalog}}',
  '{{catalog}}.governance.rls_scope_filter',
  array('provider_id'), 'row_filter_policy', array('provider_id'),
  array('account users'), array('data-governance-team'),
  true, 'Providers see only their own productivity rows (scoped by provider_id).',
  current_user(), 'APPROVED', 'governance@example.com', current_timestamp(),
  'DEMO-PROVIDER-001', 1, current_user(), current_timestamp(), current_timestamp()
);
```
> Say: *"This is the whole policy definition. Same shared function every other row filter uses."*

## 3. Generate the policy — run the job
**Workflows → `abac_apply_policies` → Run now with different parameters → `dry_run` = `false` → Run.**
Open the run → task output. Show the rendered `CREATE OR REPLACE POLICY rls_provider ... USING COLUMNS ('provider_id', col)`.
> Say: *"The governance team never writes policy DDL — the job renders and applies it, and records status."*

Confirm:
```sql
SELECT policy_name, apply_status, last_applied_at FROM {{catalog}}.governance.policy_control
WHERE policy_name='rls_provider';   -- APPLIED
```

## 4. Activate — tag the column
**Catalog → `{{catalog}}` → `gold` → `provider_productivity` → Columns → `provider_id` → Add tag → `row_filter_policy` = `provider_id`.**
Now re-run the baseline query:
```sql
SELECT provider_id, count(*) FROM {{catalog}}.gold.provider_productivity GROUP BY 1 ORDER BY 1;
```
> **0 rows.** Teaching moment: *"The policy is live, but no one is granted yet — so everyone in scope sees nothing. This is the intended fail-safe, and the framework surfaces it:"*
```sql
SELECT policy_name, health FROM {{catalog}}.governance.vw_policy_health
WHERE policy_name='rls_provider';   -- LOCKOUT_NO_GRANTS
```

## 5. Grant access — ONE row, instant, no redeploy
```sql
INSERT INTO {{catalog}}.governance.rls_user_grants
  (grant_id, email, attribute_type, attribute_value, effective_date, expiration_date,
   revoked_at, granted_by, approved_by, source_system, change_request_id, created_at)
VALUES (uuid(), current_user(), 'provider_id', 'DR004', current_date(), NULL,
        NULL, current_user(), 'governance@example.com', 'demo', 'DEMO-PROVIDER-001', current_timestamp());

SELECT provider_id, count(*) FROM {{catalog}}.gold.provider_productivity GROUP BY 1 ORDER BY 1;
```
> **Only DR004 (3 rows).** Say: *"Granting access is data, not deployment — no job re-run. Add more providers to the mapping and they appear immediately."*

## 6. Audit — "who can see what, and is anything wrong?"
```sql
-- which columns this policy governs, and its principals
SELECT * FROM {{catalog}}.governance.vw_policy_audit WHERE policy_name='rls_provider';
-- who can see which provider rows
SELECT * FROM {{catalog}}.governance.vw_effective_row_access WHERE policy_name='rls_provider';
-- overall health
SELECT * FROM {{catalog}}.governance.vw_policy_health;   -- rls_provider now OK
```

---

## Reset (run before demoing again)
```sql
DROP POLICY rls_provider ON CATALOG {{catalog}};
ALTER TABLE {{catalog}}.gold.provider_productivity ALTER COLUMN provider_id UNSET TAGS ('row_filter_policy');
DELETE FROM {{catalog}}.governance.policy_control      WHERE policy_name='rls_provider';
DELETE FROM {{catalog}}.governance.rls_user_grants WHERE attribute_type='provider_id';
```
```bash
# de-register the governed value so step 1 is real again
databricks tag-policies update-tag-policy row_filter_policy values --profile <PROFILE> --json \
'{"tag_key":"row_filter_policy","values":[{"name":"department"},{"name":"data_classification"},{"name":"department_id"},{"name":"facility_id"},{"name":"employee_id"}]}'
```

## Talking points
- One control row + one column tag = a new governed policy. No per-policy code.
- `enabled` = intent; `apply_status` = what the job actually did; views read reality.
- Policy vs grant are separate: policy = deploy step, grants = live data (no redeploy).
- The same `rls_scope_filter` serves department, employee, and provider scoping.
