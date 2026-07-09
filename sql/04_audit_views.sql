-- =============================================================================
-- 04_audit_views.sql  The audit surface, keyed on OBSERVED state (apply_status).
-- Multi-attribute aware: a policy can carry N (tag_value, attr_type) slots.
-- tag_value activates a column; attr_type is the mapping key the function checks.
-- =============================================================================

-- WHICH APPLIED POLICY hits WHICH column/table, its trigger tag value, principals, freshness.
-- A multi-attribute policy shows one row per matched column.
CREATE OR REPLACE VIEW {{catalog}}.governance.vw_policy_audit AS
SELECT
  pc.policy_name,
  pc.policy_type,
  ct.schema_name,
  ct.table_name,
  ct.column_name,
  ct.tag_name  AS trigger_tag,
  ct.tag_value AS trigger_value,
  pc.to_principals,
  pc.except_principals,
  pc.udf,
  pc.apply_status,
  pc.last_applied_at,
  pc.comment
FROM {{catalog}}.governance.policy_control pc
JOIN {{catalog}}.information_schema.column_tags ct
  ON ct.tag_name = pc.tag_key
 AND array_contains(pc.tag_values, ct.tag_value)
WHERE pc.apply_status = 'APPLIED';

-- @@
-- WHO can see WHAT rows: expands each APPLIED row-filter policy over its attr_types
-- and joins the mapping table. For a multi-attribute (OR) policy this lists grants
-- of EITHER attribute, which is exactly the union a user can see.
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
-- Operational health: desired vs observed, grant coverage (summed across the
-- policy's attributes for OR semantics), and the lockout signal.
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
             GROUP BY attribute_type) g
    ON g.attribute_type = p.attr
  GROUP BY p.policy_name
) pg ON pg.policy_name = pc.policy_name;
