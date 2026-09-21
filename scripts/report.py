"""Summarize the current CUR export without summing alternate attribution views."""

import argparse
from collections import defaultdict
import csv
from datetime import datetime, timezone
from decimal import Decimal
import gzip
import io
import json
from pathlib import Path
import sys

import boto3

ROOT = Path(__file__).resolve().parents[1]


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def summarize(rows, account, start, end):
    groups = defaultdict(lambda: {"cost": Decimal(0), "usage": Decimal(0), "rows": 0})
    account_totals = defaultdict(Decimal)
    principals = set()
    for row in rows:
        if row["line_item_usage_account_id"] != account:
            raise ValueError("Export includes an unexpected account.")
        usage_start = timestamp(row["line_item_usage_start_date"])
        if not start <= usage_start < end:
            continue
        cost = Decimal(row["line_item_unblended_cost"] or "0")
        currency = row["line_item_currency_code"]
        charge_type = row["line_item_line_item_type"]
        account_totals[(currency, charge_type)] += cost
        principal = row.get("line_item_iam_principal", "")
        tags = json.loads(row.get("tags") or "{}")
        key = (
            principal,
            tags.get("iamPrincipal/owner", ""),
            tags.get("iamPrincipal/product", ""),
            row["line_item_product_code"],
            row["line_item_operation"],
            row["line_item_usage_type"],
            row["pricing_unit"],
            currency,
            charge_type,
        )
        group = groups[key]
        group["cost"] += cost
        group["usage"] += Decimal(row["line_item_usage_amount"] or "0")
        group["rows"] += 1
        if principal:
            principals.add(principal)
    columns = ["principal", "owner", "product", "service", "operation", "usage_type", "unit", "currency", "charge_type"]
    return {
        "start": start.isoformat(), "end": end.isoformat(),
        "account_totals": [
            {"currency": currency, "charge_type": kind, "cost": str(cost)}
            for (currency, kind), cost in sorted(account_totals.items())
        ],
        "groups": [
            {**dict(zip(columns, key)), **{k: str(v) if isinstance(v, Decimal) else v for k, v in value.items()}}
            for key, value in sorted(groups.items())
        ],
        "principals": sorted(principals),
    }


def export_rows(s3, bucket):
    keys = [
        obj["Key"]
        for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix="cur/")
        for obj in page.get("Contents", [])
        if obj["Key"].endswith(".csv.gz")
    ]
    if not keys:
        raise RuntimeError("No delivered CUR data yet. Retry after AWS publishes the export.")
    for key in sorted(keys):
        response = s3.get_object(Bucket=bucket, Key=key)
        with gzip.GzipFile(fileobj=response["Body"]) as compressed:
            with io.TextIOWrapper(compressed, encoding="utf-8-sig") as text:
                yield from csv.DictReader(text)


def verify_callers(report, config):
    missing = []
    for caller, expected in config["callers"].items():
        role_name = expected["arn"].rsplit("/", 1)[-1]
        session_prefix = f"arn:aws:sts::{config['member_account_id']}:assumed-role/{role_name}/"
        rows = [
            row for row in report["groups"]
            if row["principal"] == expected["arn"] or row["principal"].startswith(session_prefix)
        ]
        expected_owner = expected["tags"].get("owner", "")
        expected_product = expected["tags"].get("product", "")
        if not rows or not any(Decimal(row["usage"]) > 0 for row in rows):
            missing.append(f"{caller}: no positive usage attributed to the role")
        elif any(
            row["owner"] != expected_owner or row["product"] != expected_product for row in rows
        ):
            missing.append(f"{caller}: unexpected owner/product attribution")
    return missing


def verify_session_run(report, config, events, run_id):
    errors = []
    attempts = [
        event for event in events
        if event.get("run_id") == run_id and event.get("event") == "attempt"
        and event.get("experiment") == "session-tags"
    ]
    successful = {
        event["id"] for event in events
        if event.get("run_id") == run_id and event.get("event") == "result" and event.get("ok")
    }
    seen_principals = set()
    for key, caller in config["session_tag_callers"].items():
        expected_tags = {**caller["tags"], **caller["session_tags"]}
        role_name = caller["arn"].rsplit("/", 1)[-1]
        prefix = f"arn:aws:sts::{config['member_account_id']}:assumed-role/{role_name}/"
        for endpoint in ("runtime", "mantle"):
            label = f"{key}/{endpoint}"
            matches = [e for e in attempts if e["caller"] == key and e["endpoint"] == endpoint]
            if len(matches) != 1 or matches[0]["id"] not in successful:
                errors.append(f"{label}: expected one successful recorded invocation")
                continue
            event = matches[0]
            principal = event["caller_arn"]
            if (
                not principal.startswith(prefix) or principal in seen_principals
                or event["role_tags"] != caller["tags"]
                or event["session_tags"] != caller["session_tags"]
            ):
                errors.append(f"{label}: ledger identity or tags do not match the experiment")
                continue
            seen_principals.add(principal)
            rows = [row for row in report["groups"] if row["principal"] == principal]
            if not rows or not any(Decimal(row["usage"]) > 0 for row in rows):
                errors.append(f"{label}: no delivered usage for the exact session ARN")
            elif any(
                row["owner"] != expected_tags.get("owner", "")
                or row["product"] != expected_tags.get("product", "")
                for row in rows
            ):
                errors.append(f"{label}: billed tags differ from expected session/static tags")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="personal-administrator")
    parser.add_argument("--config", type=Path, default=ROOT / "artifacts/test-config.json")
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--start", required=True, help="UTC billing-hour boundary, inclusive")
    parser.add_argument("--end", required=True, help="UTC billing-hour boundary, exclusive")
    parser.add_argument("--verify-callers", action="store_true",
                        help="Fail unless all four expected caller/tag combinations have delivered usage.")
    parser.add_argument("--verify-session-run", metavar="RUN_ID",
                        help="Verify all eight session-tag test calls by their exact session ARNs.")
    parser.add_argument("--ledger", type=Path, default=ROOT / "artifacts/invocations.jsonl")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    start, end = timestamp(args.start), timestamp(args.end)
    if start >= end or any(t.minute or t.second or t.microsecond for t in (start, end)):
        parser.error("Select a nonempty interval aligned to UTC billing hours.")
    session = boto3.Session(profile_name=args.profile, region_name=config["region"])
    if session.client("sts").get_caller_identity()["Account"] != config["management_account_id"]:
        parser.error("Use the configured payer account.")
    report = summarize(
        export_rows(session.client("s3"), args.bucket),
        config["member_account_id"], start, end,
    )
    if args.verify_callers:
        report["caller_validation_errors"] = verify_callers(report, config)
    if args.verify_session_run:
        events = [json.loads(line) for line in args.ledger.read_text().splitlines() if line.strip()]
        report["session_tag_run"] = args.verify_session_run
        report["session_tag_validation_errors"] = verify_session_run(
            report, config, events, args.verify_session_run
        )
    print(json.dumps(report, indent=2))
    if not report["groups"]:
        print("No billing rows in the selected interval yet.", file=sys.stderr)
        return 2
    if report.get("caller_validation_errors") or report.get("session_tag_validation_errors"):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
