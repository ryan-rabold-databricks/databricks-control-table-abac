-- ONE-TIME UPGRADE from rls_user_grants to the unified principal grant model.
-- Run 01_foundation.sql and 04_audit_views.sql immediately after this migration,
-- validate the job, and only then run 003_drop_legacy_user_grants.sql.

CREATE TABLE IF NOT EXISTS {{catalog}}.governance.rls_principal_grants (
  grant_id STRING,
  principal_type STRING,
  principal_id STRING,
  principal_name STRING,
  attribute_type STRING,
  attribute_value STRING,
  effective_date DATE,
  expiration_date DATE,
  revoked_at TIMESTAMP,
  granted_by STRING,
  approved_by STRING,
  source_system STRING,
  change_request_id STRING,
  created_at TIMESTAMP
)
COMMENT 'Unified principal-to-row-scope entitlements for users, groups, and service principals.';

-- @@
MERGE INTO {{catalog}}.governance.rls_principal_grants t
USING (
  SELECT grant_id, 'USER' AS principal_type, CAST(NULL AS STRING) AS principal_id,
         email AS principal_name, attribute_type, attribute_value, effective_date,
         expiration_date, revoked_at, granted_by, approved_by, source_system,
         change_request_id, created_at
  FROM {{catalog}}.governance.rls_user_grants
) s
ON t.grant_id = s.grant_id
WHEN NOT MATCHED THEN INSERT (
  grant_id, principal_type, principal_id, principal_name, attribute_type,
  attribute_value, effective_date, expiration_date, revoked_at, granted_by,
  approved_by, source_system, change_request_id, created_at
) VALUES (
  s.grant_id, s.principal_type, s.principal_id, s.principal_name, s.attribute_type,
  s.attribute_value, s.effective_date, s.expiration_date, s.revoked_at, s.granted_by,
  s.approved_by, s.source_system, s.change_request_id, s.created_at
);

