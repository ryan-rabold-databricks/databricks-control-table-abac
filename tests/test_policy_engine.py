import sys
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "generator"))
from policy_engine import PolicyValidationError, duplicate_policy_names, render_policy, validate_policy


def valid_row(**overrides):
    row = {
        "policy_name": "rls_provider",
        "policy_type": "ROW_FILTER",
        "scope_type": "CATALOG",
        "scope_name": "prod",
        "udf": "prod.governance.rls_scope_filter",
        "attr_types": ["provider_id"],
        "tag_key": "row_filter_policy",
        "tag_values": ["provider_id"],
        "to_principals": ["account users"],
        "except_principals": ["data-governance-team"],
        "approval_status": "APPROVED",
        "approved_by": "governance@example.com",
        "approved_at": datetime(2026, 1, 1),
        "change_request_id": "CHG-123",
        "comment": "Provider scope",
    }
    row.update(overrides)
    return row


class PolicyEngineTests(unittest.TestCase):
    def test_render_row_filter(self):
        ddl = render_policy(valid_row())
        self.assertIn("ON CATALOG `prod`", ddl)
        self.assertIn("ROW FILTER `prod`.`governance`.`rls_scope_filter`", ddl)
        self.assertIn("USING COLUMNS ('provider_id', c0)", ddl)

    def test_rejects_unapproved_policy(self):
        errors = validate_policy(valid_row(approval_status="DRAFT"))
        self.assertIn("approval_status must be APPROVED", errors)

    def test_rejects_more_than_three_matches(self):
        values = ["a", "b", "c", "d"]
        errors = validate_policy(valid_row(attr_types=values, tag_values=values))
        self.assertTrue(any("at most 3" in error for error in errors))

    def test_duplicate_detection(self):
        self.assertEqual(
            duplicate_policy_names([valid_row(), valid_row()]), {"rls_provider"}
        )


if __name__ == "__main__":
    unittest.main()

