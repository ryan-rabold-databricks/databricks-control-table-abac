-- =============================================================================
-- 01_foundation.sql
-- Standardized functions + control-plane tables for control-table-driven ABAC.
-- Schema: {{catalog}}.governance
-- Statements separated by `-- @@` for run_sql.py.
-- =============================================================================

-- Universal, type-aware column mask (one function serves every masked column).
CREATE OR REPLACE FUNCTION {{catalog}}.governance.mask_value_any(value STRING)
RETURNS STRING
COMMENT 'Universal masking function for all Databricks SQL data types (redacts to type-safe placeholder).'
RETURN CASE
  WHEN value IS NULL THEN NULL
  WHEN REGEXP_LIKE(value, '^-?[0-9]+$') THEN '-999'
  WHEN REGEXP_LIKE(value, '^-?[0-9]+\\.[0-9]+$') THEN '-999.0'
  WHEN REGEXP_LIKE(value, '^[0-9]{4}-[0-9]{2}-[0-9]{2}$') THEN '1900-01-01'
  WHEN REGEXP_LIKE(value, '^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}') THEN '1900-01-01 00:00:00'
  WHEN UPPER(value) IN ('TRUE','FALSE') THEN 'FALSE'
  WHEN LENGTH(value) > 1000 THEN '***LONG_TEXT_REDACTED***'
  ELSE '***REDACTED***'
END;

-- @@
-- Unified principal->scope mapping (tall). Adding a principal to a scope = 1 insert.
-- Adding a NEW attribute type = insert rows with a new attribute_type value
-- (no new table, no new function).
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.rls_principal_grants (
  grant_id        STRING  COMMENT 'Stable unique grant identifier.',
  principal_type  STRING  COMMENT 'USER, GROUP, or SERVICE_PRINCIPAL.',
  principal_id    STRING  COMMENT 'Immutable account identity ID when available.',
  principal_name  STRING  COMMENT 'User email, account group name, or service-principal application ID/name.',
  attribute_type  STRING  COMMENT 'e.g. department_id, provider_id, facility_id.',
  attribute_value STRING  COMMENT 'Value the principal is scoped to.',
  effective_date  DATE    COMMENT 'When this grant takes effect.',
  expiration_date DATE    COMMENT 'Optional expiry date.',
  revoked_at      TIMESTAMP COMMENT 'Revocation time; NULL means active.',
  granted_by      STRING  COMMENT 'Who added this row (audit).',
  approved_by     STRING  COMMENT 'Governance approver.',
  source_system   STRING  COMMENT 'Authoritative entitlement source.',
  change_request_id STRING COMMENT 'Approval/change evidence.',
  created_at      TIMESTAMP COMMENT 'Creation timestamp.'
)
COMMENT 'Unified principal-to-row-scope entitlements for users, groups, and service principals.';

-- @@
-- Control table: ONE ROW PER POLICY. This is the declarative surface the
-- generator renders CREATE OR REPLACE POLICY DDL from and reconciles against.
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.policy_control (
  policy_id         STRING  COMMENT 'Stable unique policy identifier.',
  policy_name       STRING  COMMENT 'Unique policy name.',
  policy_type       STRING  COMMENT 'ROW_FILTER or COLUMN_MASK.',
  scope_type        STRING  COMMENT 'CATALOG, SCHEMA, or TABLE.',
  scope_name        STRING  COMMENT 'Fully-qualified securable receiving the policy.',
  udf               STRING  COMMENT 'Fully-qualified function the policy binds.',
  attr_types        ARRAY<STRING> COMMENT 'Mapping attribute_type per slot, aligned with tag_values (NULL for masks). Length picks single- vs multi-attribute binding.',
  tag_key           STRING  COMMENT 'Governed tag key that activates this policy.',
  tag_values        ARRAY<STRING> COMMENT 'Governed tag values that activate this policy, one per matched column.',
  to_principals     ARRAY<STRING> COMMENT 'Principals the policy applies TO.',
  except_principals ARRAY<STRING> COMMENT 'Principals exempted (EXCEPT).',
  enabled           BOOLEAN COMMENT 'DESIRED state (human-set): generator applies when TRUE, drops when FALSE.',
  comment           STRING  COMMENT 'Human-readable purpose (rendered into policy COMMENT).',
  owner             STRING  COMMENT 'Accountable owner (audit).',
  approval_status   STRING  COMMENT 'DRAFT, APPROVED, REJECTED, or RETIRED.',
  approved_by       STRING  COMMENT 'Governance approver.',
  approved_at       TIMESTAMP COMMENT 'Approval timestamp.',
  change_request_id STRING  COMMENT 'Change/approval evidence identifier.',
  policy_version    INT     COMMENT 'Monotonically increasing definition version.',
  created_by        STRING  COMMENT 'Definition creator.',
  created_at        TIMESTAMP COMMENT 'Definition creation time.',
  updated_at        TIMESTAMP COMMENT 'Last change (audit).',
  apply_status      STRING  COMMENT 'OBSERVED state (generator-set): APPLIED|FAILED|SKIPPED|DISABLED|PENDING.',
  last_applied_at   TIMESTAMP COMMENT 'Last successful apply/drop (generator-set).',
  last_error        STRING  COMMENT 'Last failure detail, NULL when healthy (generator-set).'
)
COMMENT 'Declarative source of truth for ABAC policies. enabled=intent; apply_status=reality. Insert/disable a row -> run generator -> policy applied/removed.';

-- @@
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.managed_policy_inventory (
  policy_name       STRING,
  scope_type        STRING,
  scope_name        STRING,
  policy_id         STRING,
  policy_version    INT,
  ddl_hash          STRING,
  managed_by        STRING,
  first_applied_at  TIMESTAMP,
  last_applied_at   TIMESTAMP,
  retired_at        TIMESTAMP
)
COMMENT 'Ownership boundary for policies created by this framework; only inventory entries are eligible for orphan reconciliation.';

-- @@
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.policy_deployment_events (
  event_id          STRING,
  run_id            STRING,
  policy_name       STRING,
  policy_version    INT,
  action            STRING,
  outcome           STRING,
  ddl_hash          STRING,
  executed_by       STRING,
  event_time        TIMESTAMP,
  error_message     STRING
)
COMMENT 'Append-only framework deployment evidence for plans, applies, failures, and retirements.';

-- @@
-- Consolidated row-scope filter (created after its mapping table exists).
-- One function serves ALL attribute-based row scoping, data-driven via the
-- unified mapping table. p_attr_type is supplied by the policy binding;
-- p_value is the matched column value.
CREATE OR REPLACE FUNCTION {{catalog}}.governance.rls_scope_filter(
  p_attr_type STRING,
  p_value     STRING)
RETURNS BOOLEAN
COMMENT 'Row visible if the current user/service principal or one of its account groups has this grant.'
RETURN p_value IS NOT NULL AND EXISTS (
  SELECT 1
  FROM {{catalog}}.governance.rls_principal_grants m
  WHERE m.attribute_type = p_attr_type
    AND (
         (UPPER(m.principal_type) = 'GROUP' AND is_account_group_member(m.principal_name))
      OR (UPPER(m.principal_type) IN ('USER', 'SERVICE_PRINCIPAL')
          AND (LOWER(m.principal_name) = LOWER(current_user())
               OR LOWER(m.principal_id) = LOWER(current_user())))
    )
    AND m.effective_date <= current_date()
    AND (m.expiration_date IS NULL OR m.expiration_date >= current_date())
    AND m.revoked_at IS NULL
    AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(p_value))
);

-- @@
-- Two-attribute OR filter: row visible if the user is granted EITHER attribute's
-- value. Add rls_scope_filter3/... the same way for higher arity.
CREATE OR REPLACE FUNCTION {{catalog}}.governance.rls_scope_filter2(
  a1_type STRING, a1_val STRING,
  a2_type STRING, a2_val STRING)
RETURNS BOOLEAN
COMMENT 'Row visible if the current principal has a1 OR a2 directly or through an account group.'
RETURN EXISTS (
  SELECT 1
  FROM {{catalog}}.governance.rls_principal_grants m
  WHERE m.effective_date <= current_date()
    AND (m.expiration_date IS NULL OR m.expiration_date >= current_date())
    AND m.revoked_at IS NULL
    AND (
         (UPPER(m.principal_type) = 'GROUP' AND is_account_group_member(m.principal_name))
      OR (UPPER(m.principal_type) IN ('USER', 'SERVICE_PRINCIPAL')
          AND (LOWER(m.principal_name) = LOWER(current_user())
               OR LOWER(m.principal_id) = LOWER(current_user())))
    )
    AND (
         (a1_val IS NOT NULL AND m.attribute_type = a1_type
            AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(a1_val)))
      OR (a2_val IS NOT NULL AND m.attribute_type = a2_type
            AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(a2_val)))
    )
);
