import importlib.util
from pathlib import Path
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("lab", Path(__file__).parents[1] / "scripts/lab.py")
lab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lab)


class RequestBudgetTests(unittest.TestCase):
    def test_failed_or_interrupted_requests_still_consume_the_daily_allowance(self):
        with tempfile.TemporaryDirectory() as directory:
            old_artifacts, old_ledger = lab.ARTIFACTS, lab.LEDGER
            lab.ARTIFACTS = Path(directory)
            lab.LEDGER = lab.ARTIFACTS / "ledger.jsonl"
            try:
                for index in range(lab.MAX_CALLS_PER_DAY):
                    lab.append_event({"event": "attempt", "at": "2026-09-17T09:00:00Z", "id": index}, reserve=True)
                with self.assertRaises(RuntimeError):
                    lab.append_event({"event": "attempt", "at": "2026-09-17T10:00:00Z"}, reserve=True)
                lab.append_event({"event": "attempt", "at": "2026-09-18T09:00:00Z"}, reserve=True)
                self.assertEqual(len(lab.LEDGER.read_text().splitlines()), lab.MAX_CALLS_PER_DAY + 1)
            finally:
                lab.ARTIFACTS, lab.LEDGER = old_artifacts, old_ledger

    def test_resource_tags_do_not_satisfy_principal_activation(self):
        self.assertFalse(lab.principal_tags_active([
            {"TagKey": "owner", "Status": "Active"},
            {"TagKey": "product", "Status": "Active"},
        ]))
        self.assertTrue(lab.principal_tags_active([
            {"TagKey": key, "Status": "Active"} for key in lab.PRINCIPAL_KEYS
        ]))


if __name__ == "__main__":
    unittest.main()
