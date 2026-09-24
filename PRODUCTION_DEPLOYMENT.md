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
4. Load effective-dated `rls_principal_grants` before policy activation.
5. Have Governance populate `approved_by`, `approved_at`, and set `approval_status='APPROVED'`.
6. Apply the governed tag to the target row-scope column.
7. Run the job with `dry_run=true`; retain and review the output.
8. Check existing table-level filters and run `SHOW EFFECTIVE POLICIES ON TABLE <table>` for conflict analysis.
9. Run with `dry_run=false` only after approval.
10. Run `sql/06_validation.sql` plus clear, filtered, and denied persona tests.
11. Benchmark representative queries and confirm supported compute.
12. Promote the same approved version through environments. Retire or roll back by recording an approved `RETIRED` state, setting `enabled=false`, and rerunning the job.

## Principal grant grain

One row represents one principal-to-attribute-value grant. Set `principal_type` to
`USER`, `GROUP`, or `SERVICE_PRINCIPAL`; retain the immutable account identity in
`principal_id` when available and the runtime-resolvable email, group name, or application
identity in `principal_name`. The effective uniqueness key is
`(principal_type, coalesce(principal_id, principal_name), attribute_type, attribute_value)`.
Group membership is evaluated at query time, so membership changes do not require rewriting grants.

## Operational cautions

- Keep UDFs simple and benchmark entitlement lookups with realistic cardinality.
- Prefer account-group grants for broad, stable access patterns. Use `rls_principal_grants` for data-driven user, group, and service-principal entitlements.
- A maximum of three `MATCH COLUMNS` expressions is supported per policy.
- Only one distinct row-filter implementation may resolve for a given table and user.
- Review materialized-view and streaming-table run identities, time travel/cloning, OpenSharing, and AI Search limitations before rollout.
- `reconcile_orphans=true` is destructive and must be enabled only after reviewing the inventory-orphan query.
