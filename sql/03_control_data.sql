-- =============================================================================
-- 03_control_data.sql
-- Seed the declarative control plane (the "insert one row" surface) and the
-- row-scope grants. INSERT OVERWRITE makes this fully idempotent.
--
-- Principals: `account users` is built in; `data-governance-team` is an example
-- EXCEPT group — change it (and the TO groups) to principals that exist in your
-- workspace, or the generator's pre-check will SKIP the policy.
-- =============================================================================

INSERT OVERWRITE {{catalog}}.governance.policy_control
  (policy_name, policy_type, udf, attr_types, tag_key, tag_values,
   to_principals, except_principals, enabled, comment, owner, updated_at)
VALUES
  ('mask_pii', 'COLUMN_MASK',
   '{{catalog}}.governance.mask_value_any', NULL,
   'masking_rule', array('redact'),
   array('account users'), array('data-governance-team'), true,
   'Redact PII columns (ssn, email, phone, dob) for everyone except the governance team.',
   'you@example.com', current_timestamp()),

  ('rls_employee', 'ROW_FILTER',
   '{{catalog}}.governance.rls_scope_filter', array('employee_id'),
   'row_filter_policy', array('employee_id'),
   array('account users'), array('data-governance-team'), true,
   'SINGLE-ATTRIBUTE: managers see their own row plus all direct/indirect reports (hierarchy in 05).',
   'you@example.com', current_timestamp()),

  ('rls_encounter', 'ROW_FILTER',
   '{{catalog}}.governance.rls_scope_filter2', array('department_id','provider_id'),
   'row_filter_policy', array('enc_department','enc_provider'),
   array('account users'), array('data-governance-team'), true,
   'MULTI-ATTRIBUTE (OR): encounters visible if in your department OR you are the provider.',
   'you@example.com', current_timestamp());

-- @@
-- Row-scope grants for the department/provider (encounter) demo. Employee-hierarchy
-- grants are materialized separately in 05. Replace you@example.com with your own
-- login to see the encounter filter act on your session.
INSERT OVERWRITE {{catalog}}.governance.rls_user_grants VALUES
  ('you@example.com',         'department_id', 'CARDIOLOGY',  current_date(), 'demo'),
  ('you@example.com',         'provider_id',   'DR004',       current_date(), 'demo'),
  ('cardio.lead@example.com', 'department_id', 'CARDIOLOGY',  current_date(), 'demo'),
  ('ed.director@example.com', 'department_id', 'EMERGENCY',   current_date(), 'demo'),
  ('onc.chief@example.com',   'department_id', 'ONCOLOGY',    current_date(), 'demo'),
  ('multi.dept@example.com',  'department_id', 'CARDIOLOGY',  current_date(), 'demo'),
  ('multi.dept@example.com',  'department_id', 'ORTHOPEDICS', current_date(), 'demo');
