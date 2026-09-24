-- Production preflight and assurance queries. Replace {{catalog}} before use.

-- Duplicate policy identities must return zero rows.
SELECT policy_id, count(*) AS duplicates
FROM {{catalog}}.governance.policy_control
GROUP BY policy_id HAVING count(*) > 1;

-- @@
SELECT policy_name, count(*) AS duplicates
FROM {{catalog}}.governance.policy_control
GROUP BY policy_name HAVING count(*) > 1;

-- @@
-- Duplicate active entitlements must return zero rows.
SELECT email, attribute_type, attribute_value, count(*) AS duplicates
FROM {{catalog}}.governance.rls_user_grants
WHERE revoked_at IS NULL
  AND effective_date <= current_date()
  AND (expiration_date IS NULL OR expiration_date >= current_date())
GROUP BY email, attribute_type, attribute_value
HAVING count(*) > 1;

-- @@
-- Approved/enabled policies that are not observed as applied.
SELECT policy_id, policy_name, policy_version, approval_status, apply_status, last_error
FROM {{catalog}}.governance.policy_control
WHERE enabled AND (approval_status <> 'APPROVED' OR apply_status <> 'APPLIED');

-- @@
-- Inventory entries without desired-state rows. Review before reconcile_orphans=true.
SELECT i.*
FROM {{catalog}}.governance.managed_policy_inventory i
LEFT ANTI JOIN {{catalog}}.governance.policy_control c
  ON c.policy_name = i.policy_name
WHERE i.retired_at IS NULL;

-- @@
-- Recently failed deployment attempts.
SELECT *
FROM {{catalog}}.governance.policy_deployment_events
WHERE event_time >= current_timestamp() - INTERVAL 30 DAYS
  AND outcome <> 'SUCCEEDED'
ORDER BY event_time DESC;

