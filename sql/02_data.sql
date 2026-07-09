-- =============================================================================
-- 02_data.sql  Synthetic healthcare data for the demo (idempotent overwrite).
-- patient_key / encounter_key are GENERATED ALWAYS AS IDENTITY -> omitted.
-- =============================================================================

INSERT OVERWRITE {{catalog}}.gold.dim_patient
  (patient_id, first_name, last_name, date_of_birth, gender, race, ethnicity,
   primary_language, ssn, email, phone, address_line_1, city, state, zip_code,
   insurance_plan_id, primary_facility_id, is_active, created_at, updated_at, _loaded_at)
SELECT
  concat('PT', lpad(cast(id AS STRING),5,'0')),
  element_at(array('James','Mary','John','Patricia','Robert','Jennifer','Michael','Linda'), cast(rand()*8 AS int)+1),
  element_at(array('Smith','Johnson','Williams','Brown','Jones','Garcia','Miller','Davis'), cast(rand()*8 AS int)+1),
  date_sub(current_date(), cast(rand()*25000+7000 AS int)),
  element_at(array('M','F'), cast(rand()*2 AS int)+1),
  element_at(array('White','Black','Asian','Other'), cast(rand()*4 AS int)+1),
  element_at(array('Hispanic','Non-Hispanic'), cast(rand()*2 AS int)+1),
  element_at(array('English','Spanish','Mandarin'), cast(rand()*3 AS int)+1),
  concat(lpad(cast(cast(rand()*899+100 AS int) AS STRING),3,'0'),'-',lpad(cast(cast(rand()*99 AS int) AS STRING),2,'0'),'-',lpad(cast(cast(rand()*9999 AS int) AS STRING),4,'0')),
  concat('patient', cast(id AS STRING), '@example.com'),
  concat('555-', lpad(cast(cast(rand()*999 AS int) AS STRING),3,'0'),'-',lpad(cast(cast(rand()*9999 AS int) AS STRING),4,'0')),
  concat(cast(cast(rand()*9999 AS int) AS STRING),' Main St'),
  'New York', 'NY',
  lpad(cast(cast(rand()*99999 AS int) AS STRING),5,'0'),
  concat('INS', lpad(cast(cast(rand()*20 AS int) AS STRING),3,'0')),
  element_at(array('FAC01','FAC02','FAC03'), cast(rand()*3 AS int)+1),
  true,
  current_timestamp(), current_timestamp(), current_timestamp()
FROM (SELECT explode(sequence(1,50)) AS id);

-- @@
INSERT OVERWRITE {{catalog}}.gold.fact_encounter
  (encounter_id, patient_key, provider_id, facility_id, department_id, encounter_type,
   admission_date, discharge_date, primary_diagnosis, drg_code, total_charges,
   length_of_stay_days, _loaded_at)
SELECT
  concat('ENC', lpad(cast(id AS STRING),6,'0')),
  cast(rand()*50 AS int)+1,
  element_at(array('DR001','DR002','DR003','DR004','DR005','DR006','DR007','DR008','DR009','DR010'), cast(rand()*10 AS int)+1),
  element_at(array('FAC01','FAC02','FAC03'), cast(rand()*3 AS int)+1),
  element_at(array('CARDIOLOGY','EMERGENCY','ONCOLOGY','ORTHOPEDICS'), cast(rand()*4 AS int)+1),
  element_at(array('INPATIENT','OUTPATIENT','ER'), cast(rand()*3 AS int)+1),
  date_sub(current_date(), cast(rand()*365 AS int)),
  date_sub(current_date(), cast(rand()*300 AS int)),
  element_at(array('I50.9','C50.9','M17.0','J45.9','E11.9'), cast(rand()*5 AS int)+1),
  element_at(array('291','292','470','247'), cast(rand()*4 AS int)+1),
  cast(rand()*50000+1000 AS decimal(12,2)),
  cast(rand()*14+1 AS int),
  current_timestamp()
FROM (SELECT explode(sequence(1,300)) AS id);

-- @@
-- Per-provider productivity (10 providers x 3 months); used by the runbook demo.
INSERT OVERWRITE {{catalog}}.gold.provider_productivity
SELECT
  concat('DR', lpad(cast(n AS STRING),3,'0')),
  concat('Dr. ', element_at(array('Reyes','Kim','Okafor','Nguyen','Patel','Silva','Cohen','Duval','Rossi','Haas'), n)),
  element_at(array('Cardiology','Emergency','Oncology','Orthopedics'), cast(pmod(n,4) AS int)+1),
  m,
  cast(rand()*200+50 AS int),
  cast(rand()*500+100 AS decimal(10,2)),
  cast(rand()*1500+200 AS int)
FROM (SELECT explode(sequence(1,10)) AS n) providers
CROSS JOIN (SELECT explode(array(date'2026-04-01', date'2026-05-01', date'2026-06-01')) AS m) months;

-- @@
-- Column tags that ACTIVATE the policies. Governed tag values must be registered
-- first (see 00_setup.sql prerequisite). dim_patient PII -> mask_pii;
-- fact_encounter department/provider -> the combined rls_encounter (OR) policy.
ALTER TABLE {{catalog}}.gold.dim_patient ALTER COLUMN ssn           SET TAGS ('masking_rule' = 'redact');
-- @@
ALTER TABLE {{catalog}}.gold.dim_patient ALTER COLUMN email         SET TAGS ('masking_rule' = 'redact');
-- @@
ALTER TABLE {{catalog}}.gold.dim_patient ALTER COLUMN phone         SET TAGS ('masking_rule' = 'redact');
-- @@
ALTER TABLE {{catalog}}.gold.dim_patient ALTER COLUMN date_of_birth SET TAGS ('masking_rule' = 'redact');
-- @@
ALTER TABLE {{catalog}}.gold.fact_encounter ALTER COLUMN department_id SET TAGS ('row_filter_policy' = 'enc_department');
-- @@
ALTER TABLE {{catalog}}.gold.fact_encounter ALTER COLUMN provider_id   SET TAGS ('row_filter_policy' = 'enc_provider');
