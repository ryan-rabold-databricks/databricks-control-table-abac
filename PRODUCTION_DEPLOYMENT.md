# Production deployment and operation

This repository is a reference implementation. Deploy first to a non-production catalog and use synthetic personas before production data.

## Production ownership

- Governed tags: Data Governance owns definitions and allowed values; authorized stewards receive narrowly scoped `ASSIGN` and `APPLY TAG`.
- Control rows: Governance approves; the deployment identity applies.
- Job: a dedicated service principal owns and runs the job. `max_concurrent_runs: 1` prevents overlapping reconciliation.
- Policies: the framework may retire only policies recorded in `managed_policy_inventory`.

## Required privileges for the job identity

At the policy scope, grant `MANAGE` or ownership. Grant `USE CATALOG`, `USE SCHEMA`, `SELECT` and `MODIFY` on the governance schema tables, and `EXECUTE` on policy UDFs. Grant governed-tag `ASSIGN` only if the job assigns tags. Do not make the identity an account admin.

## Change process

1. Create or extend the approved governed tag and allowed values.
2. Create the grants table and policy UDF before activating a policy.
3. Insert a `policy_control` row in `DRAFT` with a stable `policy_id`, scope, version, owner, and change ID.
4. Load effective-dated `rls_user_grants` before policy activation.
5. Have Governance populate `approved_by`, `approved_at`, and set `approval_status='APPROVED'`.
6. Apply the governed tag to the target row-scope column.
7. Run the job with `dry_run=true`; retain and review the output.
8. Check existing table-level filters and run `SHOW EFFECTIVE POLICIES ON TABLE <table>` for conflict analysis.
9. Run with `dry_run=false` only after approval.
10. Run `sql/06_validation.sql` plus clear, filtered, and denied persona tests.
11. Benchmark representative queries and confirm supported compute.
12. Promote the same approved version through environments. Retire or roll back by recording an approved `RETIRED` state, setting `enabled=false`, and rerunning the job.

## Operational cautions

- Keep UDFs simple and benchmark entitlement lookups with realistic cardinality.
- Prefer account groups or identity attributes for broad, stable access patterns. Use `rls_user_grants` for genuinely data-driven user/value entitlements.
- A maximum of three `MATCH COLUMNS` expressions is supported per policy.
- Only one distinct row-filter implementation may resolve for a given table and user.
- Review materialized-view and streaming-table run identities, time travel/cloning, OpenSharing, and AI Search limitations before rollout.
- `reconcile_orphans=true` is destructive and must be enabled only after reviewing the inventory-orphan query.
