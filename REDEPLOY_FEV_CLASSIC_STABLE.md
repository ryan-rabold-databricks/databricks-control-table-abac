# Redeploy to fevm-classic-stable

Target workspace: `https://fevm-classic-stable-v61z4f.cloud.databricks.com`

The URL is currently present under three local profiles. The examples use `fevm-classic-stable`; explicitly choose a different profile if desired.

## 1. Authenticate and verify the target

```bash
databricks auth login \
  --host https://fevm-classic-stable-v61z4f.cloud.databricks.com \
  --profile fevm-classic-stable

databricks current-user me --profile fevm-classic-stable
databricks auth profiles
```

Do not continue unless `fevm-classic-stable` reports the exact target host and valid authentication.

## 2. Select deployment inputs

Choose:

- Existing development catalog
- Existing production catalog
- SQL warehouse ID used for bootstrap/migration SQL
- Dedicated production service-principal application ID
- Existing job administrator and viewer account groups

List warehouses without changing anything:

```bash
databricks warehouses list --profile fevm-classic-stable
```

The commands below use these placeholders:

```text
<DEV_CATALOG>
<PROD_CATALOG>
<WAREHOUSE_ID>
<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>
<JOB_ADMIN_GROUP>
<JOB_VIEWER_GROUP>
```

## 3. Back up and upgrade an existing deployment

Skip the migration file for a completely fresh catalog. For an existing deployment, back up the governance tables using your approved recovery procedure, then run:

```bash
python3 generator/run_sql.py sql/migrations/001_production_hardening.sql \
  --catalog <DEV_CATALOG> \
  --warehouse <WAREHOUSE_ID> \
  --profile fevm-classic-stable

python3 generator/run_sql.py sql/01_foundation.sql \
  --catalog <DEV_CATALOG> \
  --warehouse <WAREHOUSE_ID> \
  --profile fevm-classic-stable

python3 generator/run_sql.py sql/04_audit_views.sql \
  --catalog <DEV_CATALOG> \
  --warehouse <WAREHOUSE_ID> \
  --profile fevm-classic-stable
```

Migrated policies intentionally remain `DRAFT`. Review them and populate `approved_by`, `approved_at`, `change_request_id`, and `approval_status='APPROVED'` before applying.

## 4. Fresh non-production bootstrap

For a new catalog, first create the governed tags described at the top of `sql/00_setup.sql`. Use account-level tag administration and do not overwrite an existing allowed-value list without reviewing it.

Then run the fresh setup in this order:

```bash
python3 generator/run_sql.py sql/00_setup.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
python3 generator/run_sql.py sql/01_foundation.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
python3 generator/run_sql.py sql/02_data.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
python3 generator/run_sql.py sql/03_control_data.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
python3 generator/run_sql.py sql/04_audit_views.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
python3 generator/run_sql.py sql/05_employee_hierarchy.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
python3 generator/run_sql.py sql/06_validation.sql --catalog <DEV_CATALOG> --warehouse <WAREHOUSE_ID> --profile fevm-classic-stable
```

## 5. Validate and deploy the development job

```bash
databricks bundle validate --strict \
  --target dev \
  --profile fevm-classic-stable \
  --var="catalog=<DEV_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>"

databricks bundle deploy \
  --target dev \
  --profile fevm-classic-stable \
  --var="catalog=<DEV_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>"
```

Review the deployment plan before confirming it. `--auto-approve` is intentionally omitted.

## 6. Dry-run, approve, and apply in development

```bash
databricks bundle run abac_apply_policies \
  --target dev \
  --profile fevm-classic-stable \
  --var="catalog=<DEV_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>" \
  --params dry_run=true,reconcile_orphans=false
```

Review the rendered DDL, principals, scopes, warnings, and conflicts. After recorded approval:

```bash
databricks bundle run abac_apply_policies \
  --target dev \
  --profile fevm-classic-stable \
  --var="catalog=<DEV_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>" \
  --params dry_run=false,reconcile_orphans=false
```

The job verifies actual resolution with `SHOW EFFECTIVE POLICIES` after applying.

## 7. Test and promote

Run `sql/06_validation.sql`, clear/filtered/denied persona tests, tag-removal tests, an expired-grant test, and representative performance tests. Review `policy_deployment_events`, `managed_policy_inventory`, audit logs, and job run output.

After approval, bootstrap or migrate `<PROD_CATALOG>` using the same versioned SQL and deploy:

```bash
databricks bundle validate --strict \
  --target prod \
  --profile fevm-classic-stable \
  --var="catalog=<PROD_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>"

databricks bundle deploy \
  --target prod \
  --profile fevm-classic-stable \
  --var="catalog=<PROD_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>"

databricks bundle run abac_apply_policies \
  --target prod \
  --profile fevm-classic-stable \
  --var="catalog=<PROD_CATALOG>,run_as_user=<RUN_AS_SERVICE_PRINCIPAL_APPLICATION_ID>,job_admin_group=<JOB_ADMIN_GROUP>,job_viewer_group=<JOB_VIEWER_GROUP>" \
  --params dry_run=true,reconcile_orphans=false
```

Only after reviewing the production dry run should you repeat the last command with `dry_run=false`.

## 8. Orphan reconciliation

Do not enable orphan reconciliation during the initial redeployment. First run the inventory-orphan query in `sql/06_validation.sql`. If every result is approved for retirement, run the job with `reconcile_orphans=true`. Only policies recorded in `managed_policy_inventory` are eligible for deletion.

