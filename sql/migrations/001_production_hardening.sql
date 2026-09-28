-- ONE-TIME UPGRADE for an existing deployment created from the original demo.
-- Back up the governance schema and run in dev/test before production.
-- Do not run after a fresh deployment using the current 01_foundation.sql.

ALTER TABLE {{catalog}}.governance.policy_control ADD COLUMNS (
  policy_id STRING,
  scope_type STRING,
  scope_name STRING,
  approval_status STRING,
  approved_by STRING,
  approved_at TIMESTAMP,
  change_request_id STRING,
  policy_version INT,
  created_by STRING,
  created_at TIMESTAMP
);

-- @@
UPDATE {{catalog}}.governance.policy_control
SET policy_id = coalesce(policy_id, concat('MIGRATED-', policy_name)),
    scope_type = coalesce(scope_type, 'CATALOG'),
    scope_name = coalesce(scope_name, '{{catalog}}'),
    approval_status = coalesce(approval_status, 'DRAFT'),
    policy_version = coalesce(policy_version, 1),
    created_by = coalesce(created_by, owner),
    created_at = coalesce(created_at, updated_at);

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
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.managed_policy_inventory (
  policy_name STRING, scope_type STRING, scope_name STRING, policy_id STRING,
  policy_version INT, ddl_hash STRING, managed_by STRING, first_applied_at TIMESTAMP,
  last_applied_at TIMESTAMP, retired_at TIMESTAMP
);

-- @@
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.policy_deployment_events (
  event_id STRING, run_id STRING, policy_name STRING, policy_version INT,
  action STRING, outcome STRING, ddl_hash STRING, executed_by STRING,
  event_time TIMESTAMP, error_message STRING
);

-- Migrated policies intentionally remain DRAFT. A Governance approver must populate
-- approved_by, approved_at, change_request_id and set approval_status='APPROVED'.
