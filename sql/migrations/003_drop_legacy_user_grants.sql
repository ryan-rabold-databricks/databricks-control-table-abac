-- Run only after 002_principal_grants.sql, 01_foundation.sql, 04_audit_views.sql,
-- and a successful dry-run/live apply have validated rls_principal_grants.
DROP TABLE IF EXISTS {{catalog}}.governance.rls_user_grants;

