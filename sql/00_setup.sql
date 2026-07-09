-- =============================================================================
-- 00_setup.sql  Schemas and synthetic demo tables in an EXISTING catalog.
-- Run this FIRST. The target catalog is an input parameter: {{catalog}} is
-- substituted by run_sql.py from --catalog / ABAC_CATALOG (default: abac_demo).
-- The catalog must already exist and you must have CREATE on it; this script
-- does not create the catalog (catalog creation is governed separately).
--
-- PREREQUISITE — governed tags (not SQL-managed; register once via the Tag
-- Policy API or Terraform BEFORE running 02/03/05, which tag columns):
--
--   databricks tag-policies create-tag-policy --json '{
--     "tag_key":"row_filter_policy",
--     "description":"Activates ABAC row-scope policies; value names the policy-scoped trigger.",
--     "values":[{"name":"enc_department"},{"name":"enc_provider"},
--               {"name":"employee_id"},{"name":"provider_id"}]}'
--
--   databricks tag-policies create-tag-policy --json '{
--     "tag_key":"masking_rule",
--     "description":"Activates ABAC column-mask policies.",
--     "values":[{"name":"redact"}]}'
--
-- (If the account tag-policy quota is full, reuse/extend an owned governed tag
--  via `databricks tag-policies update-tag-policy`.)
-- =============================================================================

CREATE SCHEMA IF NOT EXISTS {{catalog}}.gold
  COMMENT 'Business-ready demo tables (synthetic).';

-- @@
CREATE SCHEMA IF NOT EXISTS {{catalog}}.healthcare_demo
  COMMENT 'Additional demo tables (synthetic).';

-- @@
CREATE SCHEMA IF NOT EXISTS {{catalog}}.governance
  COMMENT 'Governance control plane: control table, grants, functions, audit views.';

-- @@
CREATE TABLE IF NOT EXISTS {{catalog}}.gold.dim_patient (
  patient_key         BIGINT GENERATED ALWAYS AS IDENTITY,
  patient_id          STRING,
  first_name          STRING,
  last_name           STRING,
  date_of_birth       DATE,
  gender              STRING,
  race                STRING,
  ethnicity           STRING,
  primary_language    STRING,
  ssn                 STRING,
  email               STRING,
  phone               STRING,
  address_line_1      STRING,
  city                STRING,
  state               STRING,
  zip_code            STRING,
  insurance_plan_id   STRING,
  primary_facility_id STRING,
  is_active           BOOLEAN,
  created_at          TIMESTAMP,
  updated_at          TIMESTAMP,
  _loaded_at          TIMESTAMP
) COMMENT 'Synthetic patient dimension; PII columns are masked via mask_pii.';

-- @@
CREATE TABLE IF NOT EXISTS {{catalog}}.gold.fact_encounter (
  encounter_key       BIGINT GENERATED ALWAYS AS IDENTITY,
  encounter_id        STRING,
  patient_key         BIGINT,
  provider_id         STRING,
  facility_id         STRING,
  department_id       STRING,
  encounter_type      STRING,
  admission_date      DATE,
  discharge_date      DATE,
  primary_diagnosis   STRING,
  drg_code            STRING,
  total_charges       DECIMAL(12,2),
  length_of_stay_days INT,
  _loaded_at          TIMESTAMP
) COMMENT 'Synthetic encounter fact; row-filtered by department OR provider via rls_encounter.';

-- @@
CREATE TABLE IF NOT EXISTS {{catalog}}.gold.provider_productivity (
  provider_id   STRING,
  provider_name STRING,
  specialty     STRING,
  report_month  DATE,
  encounters    INT,
  rvus          DECIMAL(10,2),
  panel_size    INT
) COMMENT 'Synthetic per-provider productivity; used by DEMO_add_provider_filter.md.';
