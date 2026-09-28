-- =============================================================================
-- 05_employee_hierarchy.sql
-- Proves the HARD case: rls_employee (recursive reporting hierarchy) driven from
-- the SAME rls_scope_filter, by pre-expanding the reporting tree into the flat
-- grants table. The recursion lives in this materialization step, NOT in the
-- policy or the filter function. The rls_employee control row is seeded in 03;
-- this file supplies its data plane (hierarchy, closure grants, tagged column).
-- =============================================================================

-- Org chart (adjacency list). Stands in for a Workday/HR hierarchy source.
CREATE TABLE IF NOT EXISTS {{catalog}}.governance.employee_hierarchy (
  employee_id         STRING,
  employee_email      STRING,
  manager_employee_id STRING,
  full_name           STRING
) COMMENT 'Source org chart (adjacency list) for rls_employee expansion.';

-- @@
-- Assumes an acyclic tree (a real hierarchy). A cycle would recurse to the
-- engine's recursion limit; guard/validate upstream for production sources.
INSERT OVERWRITE {{catalog}}.governance.employee_hierarchy VALUES
  ('E001','ceo@example.com',    NULL,  'Cora Chief'),
  ('E002','you@example.com',    'E001','Morgan Lee'),     -- mid-level manager; set to your own login to demo as yourself
  ('E003','alice@example.com',  'E002','Alice Adams'),
  ('E004','bob@example.com',    'E002','Bob Barnes'),
  ('E005','carol@example.com',  'E003','Carol Chen'),
  ('E006','dave@example.com',   'E003','Dave Diaz'),
  ('E007','erin@example.com',   'E001','Erin Ericson'),   -- peer of E002 (not in E002's subtree)
  ('E008','frank@example.com',  'E007','Frank Foster'),
  ('E009','grace@example.com',  'E007','Grace Gomez'),
  ('E010','heidi@example.com',  'E004','Heidi Hughes');   -- indirect report of E002 (via E004)

-- @@
-- Sensitive per-employee table we will row-filter (one row per employee).
CREATE TABLE IF NOT EXISTS {{catalog}}.healthcare_demo.employee_compensation (
  employee_id   STRING,
  employee_name STRING,
  department    STRING,
  base_salary   DECIMAL(12,2),
  bonus         DECIMAL(12,2)
) COMMENT 'HR compensation, row-filtered by reporting hierarchy via rls_employee.';

-- @@
INSERT OVERWRITE {{catalog}}.healthcare_demo.employee_compensation
SELECT
  h.employee_id, h.full_name,
  element_at(array('Cardiology','Emergency','Oncology','Finance'), cast(rand()*4 AS int)+1),
  cast(rand()*120000+80000 AS decimal(12,2)),
  cast(rand()*30000 AS decimal(12,2))
FROM {{catalog}}.governance.employee_hierarchy h;

-- @@
-- MATERIALIZE the transitive closure into the unified grants table.
-- For each manager (root_email), every employee_id in their subtree incl. self.
-- This is the ONLY place the hierarchy recursion runs; re-runnable (delete+insert).
DELETE FROM {{catalog}}.governance.rls_principal_grants WHERE attribute_type = 'employee_id';

-- @@
INSERT INTO {{catalog}}.governance.rls_principal_grants
  (grant_id, principal_type, principal_id, principal_name, attribute_type, attribute_value, effective_date, expiration_date,
   revoked_at, granted_by, approved_by, source_system, change_request_id, created_at)
WITH RECURSIVE subtree AS (
  SELECT employee_id AS root_id, employee_email AS root_email, employee_id AS descendant_id
  FROM {{catalog}}.governance.employee_hierarchy
  UNION ALL
  SELECT s.root_id, s.root_email, h.employee_id
  FROM subtree s
  JOIN {{catalog}}.governance.employee_hierarchy h
    ON h.manager_employee_id = s.descendant_id
)
SELECT uuid(), 'USER', NULL, root_email, 'employee_id', descendant_id, current_date(), NULL, NULL,
       'hierarchy_job', 'governance@example.com', 'employee_hierarchy', 'DEMO-002', current_timestamp()
FROM subtree
WHERE root_email IS NOT NULL;

-- @@
-- Activate: tag the employee_id column (governed value 'employee_id' must be registered).
ALTER TABLE {{catalog}}.healthcare_demo.employee_compensation
  ALTER COLUMN employee_id SET TAGS ('row_filter_policy' = 'employee_id');
