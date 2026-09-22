"""Create ignored Terraform inputs after checking the existing organization."""

import argparse
import json
import os
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]


def write_private(path, text):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write(text)
    os.chmod(path, 0o600)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="personal-administrator")
    parser.add_argument("--management-account-id", required=True)
    parser.add_argument("--member-account-id", required=True)
    parser.add_argument("--region", default="eu-central-1")
    parser.add_argument("--console-user-name", default="vl.iliakov")
    args = parser.parse_args()
    if not args.region.startswith("eu-"):
        parser.error("This lab keeps inference and storage in Europe.")
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    identity = session.client("sts").get_caller_identity()
    organization = session.client("organizations").describe_organization()["Organization"]
    if identity["Account"] != args.management_account_id:
        parser.error("The selected profile is not in the expected management account.")
    if organization["MasterAccountId"] != args.management_account_id:
        parser.error("The selected account is not the organization's management account.")
    accounts = [
        a
        for page in session.client("organizations").get_paginator("list_accounts").paginate()
        for a in page["Accounts"]
    ]
    if not any(
        a["Id"] == args.member_account_id and a.get("State", a.get("Status")) == "ACTIVE"
        for a in accounts
    ) or args.member_account_id == args.management_account_id:
        parser.error("Select an active member of this organization.")
    session.client("sts").assume_role(
        RoleArn=f"arn:aws:iam::{args.member_account_id}:role/OrganizationAccountAccessRole",
        RoleSessionName="cost-lab-configuration",
    )
    common = {"management_account_id": args.management_account_id, "region": args.region}
    values = {
        "bootstrap": common,
        "billing": {
            **common,
            "member_account_id": args.member_account_id,
            "budget_email": organization["MasterAccountEmail"],
        },
        "sso-lab": {**common, "member_account_id": args.member_account_id},
        "billing-tags": {"management_account_id": args.management_account_id},
        "account-access": {
            **common,
            "member_account_id": args.member_account_id,
            "console_user_name": args.console_user_name,
        },
    }
    bucket = f"aws-infra-state-{args.management_account_id}-{args.region}"
    for name, variables in values.items():
        write_private(ROOT / name / "local.auto.tfvars.json", json.dumps(variables, indent=2) + "\n")
        backend = {
            "bucket": bucket,
            "key": f"{name}/terraform.tfstate",
            "region": args.region,
            "profile": args.profile,
            "encrypt": True,
            "use_lockfile": True,
            "allowed_account_ids": [args.management_account_id],
        }
        write_private(
            ROOT / name / "local.backend.hcl",
            "".join(f"{key} = {json.dumps(value)}\n" for key, value in backend.items()),
        )
    print("Verified organization and member access; wrote ignored inputs and backend settings.")


if __name__ == "__main__":
    main()
