from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("report", Path(__file__).parents[1] / "scripts/report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class ReportingTests(unittest.TestCase):
    def row(self, **changes):
        return {
            "line_item_usage_account_id": "123456789012",
            "line_item_usage_start_date": "2026-09-17T09:00:00Z",
            "line_item_unblended_cost": "0.000001",
            "line_item_currency_code": "USD",
            "line_item_line_item_type": "Usage",
            "line_item_iam_principal": "arn:aws:iam::123456789012:role/test",
            "tags": '{"iamPrincipal/owner":"alice","iamPrincipal/product":"lab","resource/owner":"different"}',
            "line_item_product_code": "AmazonBedrock",
            "line_item_operation": "InvokeModel",
            "line_item_usage_type": "input-tokens",
            "pricing_unit": "tokens",
            "line_item_usage_amount": "10",
            **changes,
        }

    def summarize(self, rows):
        return report.summarize(
            rows, "123456789012",
            datetime(2026, 9, 17, 9, tzinfo=timezone.utc),
            datetime(2026, 9, 17, 10, tzinfo=timezone.utc),
        )

    def test_principal_tags_and_decimal_precision(self):
        result = self.summarize([self.row(), self.row()])
        self.assertEqual(result["groups"][0]["owner"], "alice")
        self.assertEqual(result["groups"][0]["cost"], "0.000002")
        self.assertEqual(result["account_totals"][0]["cost"], "0.000002")

    def test_untagged_usage_and_distinct_token_units_are_preserved(self):
        result = self.summarize([
            self.row(tags="{}", line_item_iam_principal=""),
            self.row(line_item_usage_type="output-tokens"),
        ])
        self.assertEqual(len(result["groups"]), 2)
        self.assertEqual(result["groups"][0]["owner"], "")
        self.assertEqual(result["groups"][0]["rows"], 1)

    def test_end_exclusive_and_unexpected_account_rejected(self):
        self.assertEqual(self.summarize([self.row(line_item_usage_start_date="2026-09-17T10:00:00Z")])["groups"], [])
        with self.assertRaises(ValueError):
            self.summarize([self.row(line_item_usage_account_id="999999999999")])

    def test_offset_is_converted_before_billing_window_filter(self):
        result = self.summarize([self.row(line_item_usage_start_date="2026-09-17T11:00:00+02:00")])
        self.assertEqual(len(result["groups"]), 1)

    def test_caller_verification_accepts_assumed_roles_but_rejects_wrong_tags(self):
        config = {
            "member_account_id": "123456789012",
            "callers": {
                "test": {
                    "arn": "arn:aws:iam::123456789012:role/test",
                    "tags": {"owner": "alice", "product": "lab"},
                }
            },
        }
        result = self.summarize([
            self.row(line_item_iam_principal="arn:aws:sts::123456789012:assumed-role/test/session")
        ])
        self.assertEqual(report.verify_callers(result, config), [])
        result["groups"][0]["owner"] = "bob"
        self.assertEqual(len(report.verify_callers(result, config)), 1)
        self.assertEqual(len(report.verify_callers({"groups": []}, config)), 1)


if __name__ == "__main__":
    unittest.main()
