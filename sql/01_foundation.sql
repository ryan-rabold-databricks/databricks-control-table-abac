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
-- Unified identity->scope mapping (tall). Adding a person to a scope = 1 insert.
-- Adding a NEW attribute type = insert rows with a new attribute_type value
-- (no new table, no new function).
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.rls_user_grants (
  email           STRING  COMMENT 'Principal (matches current_user()).',
  attribute_type  STRING  COMMENT 'e.g. department_id, provider_id, facility_id.',
  attribute_value STRING  COMMENT 'Value the principal is scoped to.',
  effective_date  DATE    COMMENT 'When this grant took effect.',
  granted_by      STRING  COMMENT 'Who added this row (audit).'
)
COMMENT 'Unified RBAC mapping: current_user() -> (attribute_type, attribute_value). Single source for all row-scope grants.';

-- @@
-- Control table: ONE ROW PER POLICY. This is the declarative surface the
-- generator renders CREATE OR REPLACE POLICY DDL from and reconciles against.
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.policy_control (
  policy_name       STRING  COMMENT 'Unique policy name.',
  policy_type       STRING  COMMENT 'ROW_FILTER or COLUMN_MASK.',
  udf               STRING  COMMENT 'Fully-qualified function the policy binds.',
  attr_types        ARRAY<STRING> COMMENT 'Mapping attribute_type per slot, aligned with tag_values (NULL for masks). Length picks single- vs multi-attribute binding.',
  tag_key           STRING  COMMENT 'Governed tag key that activates this policy.',
  tag_values        ARRAY<STRING> COMMENT 'Governed tag values that activate this policy, one per matched column.',
  to_principals     ARRAY<STRING> COMMENT 'Principals the policy applies TO.',
  except_principals ARRAY<STRING> COMMENT 'Principals exempted (EXCEPT).',
  enabled           BOOLEAN COMMENT 'DESIRED state (human-set): generator applies when TRUE, drops when FALSE.',
  comment           STRING  COMMENT 'Human-readable purpose (rendered into policy COMMENT).',
  owner             STRING  COMMENT 'Accountable owner (audit).',
  updated_at        TIMESTAMP COMMENT 'Last change (audit).',
  apply_status      STRING  COMMENT 'OBSERVED state (generator-set): APPLIED|FAILED|SKIPPED|DISABLED|PENDING.',
  last_applied_at   TIMESTAMP COMMENT 'Last successful apply/drop (generator-set).',
  last_error        STRING  COMMENT 'Last failure detail, NULL when healthy (generator-set).'
)
COMMENT 'Declarative source of truth for ABAC policies. enabled=intent; apply_status=reality. Insert/disable a row -> run generator -> policy applied/removed.';

-- @@
-- Consolidated row-scope filter (created after its mapping table exists).
-- One function serves ALL attribute-based row scoping, data-driven via the
-- unified mapping table. p_attr_type is supplied by the policy binding;
-- p_value is the matched column value.
CREATE OR REPLACE FUNCTION {{catalog}}.governance.rls_scope_filter(
  p_attr_type STRING,
  p_value     STRING)
RETURNS BOOLEAN
COMMENT 'Row visible if current_user() is mapped to this attribute value in rls_user_grants.'
RETURN p_value IS NOT NULL AND EXISTS (
  SELECT 1
  FROM {{catalog}}.governance.rls_user_grants m
  WHERE m.email = current_user()
    AND m.attribute_type = p_attr_type
    AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(p_value))
);

-- @@
-- Two-attribute OR filter: row visible if the user is granted EITHER attribute's
-- value. Add rls_scope_filter3/... the same way for higher arity.
CREATE OR REPLACE FUNCTION {{catalog}}.governance.rls_scope_filter2(
  a1_type STRING, a1_val STRING,
  a2_type STRING, a2_val STRING)
RETURNS BOOLEAN
COMMENT 'Row visible if current_user() is mapped to a1 OR a2 in rls_user_grants (OR-composed).'
RETURN EXISTS (
  SELECT 1
  FROM {{catalog}}.governance.rls_user_grants m
  WHERE m.email = current_user()
    AND (
         (a1_val IS NOT NULL AND m.attribute_type = a1_type
            AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(a1_val)))
      OR (a2_val IS NOT NULL AND m.attribute_type = a2_type
            AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(a2_val)))
    )
);
