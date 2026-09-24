-- =============================================================================
-- 07_rename.sql  Rename row-filter objects to the rls_* convention.
--   rbac_user_attributes -> rls_user_grants   (table; row-filter grant substrate)
--   rbac_scope_filter     -> rls_scope_filter    (1-attribute filter)
--   rbac_scope_filter2    -> rls_scope_filter2   (2-attribute OR filter)
-- This migration also adds the grant-lifecycle columns required by its UDFs.
-- Run order: this file, THEN 002_principal_grants.sql, refresh foundation/views,
-- run the generator, validate, and finally run 003_drop_legacy_user_grants.sql.
-- =============================================================================

-- Rename the grants table (preserves data + history).
ALTER TABLE {{catalog}}.governance.rbac_user_attributes
  RENAME TO {{catalog}}.governance.rls_user_grants;

-- @@
ALTER TABLE {{catalog}}.governance.rls_user_grants ADD COLUMNS (
  grant_id STRING,
  expiration_date DATE,
  revoked_at TIMESTAMP,
  approved_by STRING,
  source_system STRING,
  change_request_id STRING,
  created_at TIMESTAMP
);

-- @@
UPDATE {{catalog}}.governance.rls_user_grants
SET grant_id = coalesce(
      grant_id,
      sha2(concat_ws('||', email, attribute_type, attribute_value,
                     cast(effective_date AS STRING), granted_by), 256)
    ),
    source_system = coalesce(source_system, 'migrated_demo'),
    created_at = coalesce(created_at, cast(effective_date AS TIMESTAMP));

-- @@
-- Recreate the filter functions under rls_* names, pointing at rls_user_grants.
CREATE OR REPLACE FUNCTION {{catalog}}.governance.rls_scope_filter(
  p_attr_type STRING,
  p_value     STRING)
RETURNS BOOLEAN
COMMENT 'Row visible if current_user() is granted this attribute value in rls_user_grants.'
RETURN p_value IS NOT NULL AND EXISTS (
  SELECT 1
  FROM {{catalog}}.governance.rls_user_grants m
  WHERE m.email = current_user()
    AND m.attribute_type = p_attr_type
    AND m.effective_date <= current_date()
    AND (m.expiration_date IS NULL OR m.expiration_date >= current_date())
    AND m.revoked_at IS NULL
    AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(p_value))
);

-- @@
CREATE OR REPLACE FUNCTION {{catalog}}.governance.rls_scope_filter2(
  a1_type STRING, a1_val STRING,
  a2_type STRING, a2_val STRING)
RETURNS BOOLEAN
COMMENT 'Row visible if current_user() is granted a1 OR a2 in rls_user_grants (OR-composed).'
RETURN EXISTS (
  SELECT 1
  FROM {{catalog}}.governance.rls_user_grants m
  WHERE m.email = current_user()
    AND m.effective_date <= current_date()
    AND (m.expiration_date IS NULL OR m.expiration_date >= current_date())
    AND m.revoked_at IS NULL
    AND (
         (a1_val IS NOT NULL AND m.attribute_type = a1_type
            AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(a1_val)))
      OR (a2_val IS NOT NULL AND m.attribute_type = a2_type
            AND TRIM(LOWER(m.attribute_value)) = TRIM(LOWER(a2_val)))
    )
);

-- @@
-- Repoint the control table to the new udf names (handles both filter1 and filter2).
UPDATE {{catalog}}.governance.policy_control
SET udf = replace(udf, 'rbac_scope_filter', 'rls_scope_filter'),
    updated_at = current_timestamp()
WHERE udf LIKE '%rbac_scope_filter%';

-- @@
-- Recreate the two views that referenced the old table name.
CREATE OR REPLACE VIEW {{catalog}}.governance.vw_effective_row_access AS
SELECT
  m.email            AS principal,
  pe.policy_name,
  pe.attr            AS attribute_type,
  m.attribute_value  AS allowed_value,
  m.granted_by,
  m.effective_date
FROM (
  SELECT policy_name, explode(attr_types) AS attr
  FROM {{catalog}}.governance.policy_control
  WHERE apply_status = 'APPLIED' AND policy_type = 'ROW_FILTER'
) pe
JOIN {{catalog}}.governance.rls_user_grants m
  ON m.attribute_type = pe.attr;

-- @@
CREATE OR REPLACE VIEW {{catalog}}.governance.vw_policy_health AS
SELECT
  pc.policy_name,
  pc.policy_type,
  pc.enabled        AS desired_enabled,
  pc.apply_status   AS observed_status,
  pc.last_applied_at,
  pc.last_error,
  pc.attr_types,
  CASE WHEN pc.policy_type = 'ROW_FILTER' THEN coalesce(pg.grant_count, 0) END AS grant_count,
  CASE
    WHEN pc.enabled AND pc.apply_status <> 'APPLIED'                          THEN 'NOT_IN_EFFECT'
    WHEN NOT pc.enabled AND pc.apply_status = 'APPLIED'                       THEN 'PENDING_DISABLE'
    WHEN pc.policy_type = 'ROW_FILTER' AND pc.apply_status = 'APPLIED'
         AND coalesce(pg.grant_count, 0) = 0                                  THEN 'LOCKOUT_NO_GRANTS'
    ELSE 'OK'
  END AS health
FROM {{catalog}}.governance.policy_control pc
LEFT JOIN (
  SELECT p.policy_name, sum(coalesce(g.c, 0)) AS grant_count
  FROM (SELECT policy_name, explode(attr_types) AS attr
        FROM {{catalog}}.governance.policy_control
        WHERE policy_type = 'ROW_FILTER') p
  LEFT JOIN (SELECT attribute_type, count(*) c
             FROM {{catalog}}.governance.rls_user_grants
             WHERE revoked_at IS NULL
               AND effective_date <= current_date()
               AND (expiration_date IS NULL OR expiration_date >= current_date())
             GROUP BY attribute_type) g
    ON g.attribute_type = p.attr
  GROUP BY p.policy_name
) pg ON pg.policy_name = pc.policy_name;
