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
  (policy_id, policy_name, policy_type, scope_type, scope_name, udf, attr_types, tag_key, tag_values,
   to_principals, except_principals, enabled, comment, owner, approval_status, approved_by,
   approved_at, change_request_id, policy_version, created_by, created_at, updated_at)
VALUES
  ('POL-001', 'mask_pii', 'COLUMN_MASK', 'CATALOG', '{{catalog}}',
   '{{catalog}}.governance.mask_value_any', NULL,
   'masking_rule', array('redact'),
   array('account users'), array('data-governance-team'), true,
   'Redact PII columns (ssn, email, phone, dob) for everyone except the governance team.',
   'you@example.com', 'APPROVED', 'governance@example.com', current_timestamp(), 'DEMO-001', 1,
   current_user(), current_timestamp(), current_timestamp()),

  ('POL-002', 'rls_employee', 'ROW_FILTER', 'CATALOG', '{{catalog}}',
   '{{catalog}}.governance.rls_scope_filter', array('employee_id'),
   'row_filter_policy', array('employee_id'),
   array('account users'), array('data-governance-team'), true,
   'SINGLE-ATTRIBUTE: managers see their own row plus all direct/indirect reports (hierarchy in 05).',
   'you@example.com', 'APPROVED', 'governance@example.com', current_timestamp(), 'DEMO-002', 1,
   current_user(), current_timestamp(), current_timestamp()),

  ('POL-003', 'rls_encounter', 'ROW_FILTER', 'CATALOG', '{{catalog}}',
   '{{catalog}}.governance.rls_scope_filter2', array('department_id','provider_id'),
   'row_filter_policy', array('enc_department','enc_provider'),
   array('account users'), array('data-governance-team'), true,
   'MULTI-ATTRIBUTE (OR): encounters visible if in your department OR you are the provider.',
   'you@example.com', 'APPROVED', 'governance@example.com', current_timestamp(), 'DEMO-003', 1,
   current_user(), current_timestamp(), current_timestamp());

-- @@
-- Row-scope grants for the department/provider (encounter) demo. Employee-hierarchy
-- grants are materialized separately in 05. Replace you@example.com with your own
-- login to see the encounter filter act on your session.
INSERT OVERWRITE {{catalog}}.governance.rls_principal_grants
  (grant_id, principal_type, principal_id, principal_name, attribute_type, attribute_value, effective_date, expiration_date,
   revoked_at, granted_by, approved_by, source_system, change_request_id, created_at)
VALUES
  ('G-001','USER',NULL,'you@example.com',         'department_id','CARDIOLOGY', current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp()),
  ('G-002','USER',NULL,'you@example.com',         'provider_id','DR004', current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp()),
  ('G-003','USER',NULL,'cardio.lead@example.com', 'department_id','CARDIOLOGY', current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp()),
  ('G-004','USER',NULL,'ed.director@example.com', 'department_id','EMERGENCY', current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp()),
  ('G-005','USER',NULL,'onc.chief@example.com',   'department_id','ONCOLOGY', current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp()),
  ('G-006','USER',NULL,'multi.dept@example.com',  'department_id','CARDIOLOGY', current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp()),
  ('G-007','USER',NULL,'multi.dept@example.com',  'department_id','ORTHOPEDICS',current_date(),NULL,NULL,'demo','governance@example.com','demo','DEMO-003',current_timestamp());
