# Control-table-driven ABAC

This project deploys Unity Catalog ABAC policy automation with Databricks Declarative Automation Bundles.

## Required workflow

- Use an explicit Databricks CLI profile for every workspace operation.
- Validate bundles with `databricks bundle validate --strict` before deployment.
- Run policy changes with `dry_run=true`, review the rendered plan, then run with `dry_run=false`.
- Never bypass the `APPROVED` control-table state in production.
- Do not delete policies unless they are recorded in `managed_policy_inventory` as owned by this framework.
- Keep `generator/policy_engine.py` as the single implementation of validation and DDL rendering.

## Verification

Run `python3 -m unittest discover -s tests -v` after changing policy rendering or validation.

