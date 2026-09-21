import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("report", Path(__file__).parents[1] / "scripts/report.py")
report = importlib.util.module_from_spec(spec)
spec.loader.exec_module(report)


class SessionReportingTests(unittest.TestCase):
    def experiment(self):
        callers = {
            "alice": {"arn": "arn:aws:iam::123456789012:role/shared", "tags": {},
                      "session_tags": {"owner": "alice", "product": "a"}},
            "bob": {"arn": "arn:aws:iam::123456789012:role/shared", "tags": {},
                    "session_tags": {"owner": "bob", "product": "b"}},
            "untagged": {"arn": "arn:aws:iam::123456789012:role/shared", "tags": {}, "session_tags": {}},
            "static": {"arn": "arn:aws:iam::123456789012:role/static",
                       "tags": {"owner": "control", "product": "control"}, "session_tags": {}},
        }
        events, rows = [], []
        for key, caller in callers.items():
            for endpoint in ("runtime", "mantle"):
                name = f"{key}-{endpoint}"
                role = caller["arn"].rsplit("/", 1)[-1]
                principal = f"arn:aws:sts::123456789012:assumed-role/{role}/{name}"
                event = {
                    "run_id": "test", "experiment": "session-tags", "id": name,
                    "caller": key, "endpoint": endpoint, "caller_arn": principal,
                    "role_tags": caller["tags"], "session_tags": caller["session_tags"],
                }
                events.extend([{**event, "event": "attempt"}, {**event, "event": "result", "ok": True}])
                tags = {**caller["tags"], **caller["session_tags"]}
                rows.append({"principal": principal, "usage": "1",
                             "owner": tags.get("owner", ""), "product": tags.get("product", "")})
        return {"groups": rows}, {"member_account_id": "123456789012", "session_tag_callers": callers}, events

    def test_distinct_sessions_of_one_role_are_not_conflated(self):
        summary, config, events = self.experiment()
        self.assertEqual(report.verify_session_run(summary, config, events, "test"), [])
        summary["groups"][0]["owner"] = "bob"
        self.assertEqual(len(report.verify_session_run(summary, config, events, "test")), 1)

    def test_static_only_billing_does_not_pass_session_attribution(self):
        summary, config, events = self.experiment()
        for row in summary["groups"][:4]:
            row.update(owner="", product="")
        self.assertEqual(len(report.verify_session_run(summary, config, events, "test")), 4)

    def test_missing_endpoint_control_or_run_cannot_pass(self):
        summary, config, events = self.experiment()
        summary["groups"].pop()
        self.assertEqual(len(report.verify_session_run(summary, config, events, "test")), 1)
        self.assertEqual(len(report.verify_session_run(summary, config, events, "missing")), 8)
        events[1]["ok"] = False
        self.assertEqual(len(report.verify_session_run(summary, config, events, "test")), 2)


if __name__ == "__main__":
    unittest.main()
