#!/usr/bin/env python3
"""Execute a .sql file statement-by-statement against a Databricks SQL warehouse.

Statements are separated by a line containing only `-- @@`. This avoids the
semicolon-splitting problem with SQL UDF bodies (CASE ... END; contains ';').

The `{{catalog}}` placeholder in the .sql file is substituted with --catalog
(default from ABAC_CATALOG), so the catalog is an input parameter and must
already exist — this framework never creates a catalog.

Usage:
    python run_sql.py <file.sql> [--profile P] [--warehouse ID] [--catalog C]

Execution is driven through the Databricks CLI (`databricks api post`), so no
Python SDK dependency is required. Idempotent by construction: every DDL file in
this demo uses CREATE OR REPLACE / CREATE ... IF NOT EXISTS / INSERT OVERWRITE.
"""
import argparse
import json
import os
import subprocess
import sys
import time

# Configure via environment or CLI flags (no environment-specific defaults baked in).
WAREHOUSE_DEFAULT = os.environ.get("DATABRICKS_WAREHOUSE_ID", "")
PROFILE_DEFAULT = os.environ.get("DATABRICKS_CONFIG_PROFILE", "")
CATALOG_DEFAULT = os.environ.get("ABAC_CATALOG", "abac_demo")


def run_statement(stmt, profile, warehouse, catalog):
    payload = {
        "warehouse_id": warehouse,
        "catalog": catalog,
        "wait_timeout": "30s",
        "on_wait_timeout": "CONTINUE",
        "statement": stmt,
    }
    proc = subprocess.run(
        ["databricks", "api", "post", "/api/2.0/sql/statements",
         "--profile", profile, "--json", json.dumps(payload)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return False, proc.stderr.strip() or proc.stdout.strip()
    resp = json.loads(proc.stdout)
    state = resp.get("status", {}).get("state")
    statement_id = resp.get("statement_id")
    deadline = time.time() + 1800
    while state in ("PENDING", "RUNNING") and time.time() < deadline:
        time.sleep(3)
        poll = subprocess.run(
            ["databricks", "api", "get", f"/api/2.0/sql/statements/{statement_id}",
             "--profile", profile], capture_output=True, text=True,
        )
        if poll.returncode != 0:
            return False, poll.stderr.strip() or poll.stdout.strip()
        resp = json.loads(poll.stdout)
        state = resp.get("status", {}).get("state")
    if state == "SUCCEEDED":
        result = resp.get("result") or {}
        return True, result.get("data_array")
    return False, resp.get("status", {}).get("error", {}).get("message", str(resp.get("status")))


def split_statements(text):
    return [s.strip() for s in text.split("\n-- @@") if s.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--profile", default=PROFILE_DEFAULT)
    ap.add_argument("--warehouse", default=WAREHOUSE_DEFAULT)
    ap.add_argument("--catalog", default=CATALOG_DEFAULT)
    args = ap.parse_args()
    if not args.profile:
        ap.error("--profile is required (or set DATABRICKS_CONFIG_PROFILE explicitly)")
    if not args.warehouse:
        ap.error("--warehouse is required (or set DATABRICKS_WAREHOUSE_ID explicitly)")

    with open(args.file) as f:
        # Catalog is an input parameter: substitute the {{catalog}} placeholder so
        # the SQL targets an existing catalog (this framework never creates one).
        text = f.read().replace("{{catalog}}", args.catalog)
    statements = split_statements(text)

    ok = 0
    for i, stmt in enumerate(statements, 1):
        label = " ".join(stmt.split())[:70]
        success, data = run_statement(stmt, args.profile, args.warehouse, args.catalog)
        if success:
            ok += 1
            print(f"[{i}/{len(statements)}] OK   {label}")
            if data:
                for row in data[:20]:
                    print("        ", row)
        else:
            print(f"[{i}/{len(statements)}] FAIL {label}")
            print(f"         -> {data}")
            sys.exit(1)
    print(f"\nDone: {ok}/{len(statements)} statements succeeded.")


if __name__ == "__main__":
    main()
