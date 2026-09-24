-- Resume helper for a deployment where 001_production_hardening.sql stopped
-- after adding rls_user_grants columns but before completing the backfill.
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
