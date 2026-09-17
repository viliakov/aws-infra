"""Bounded Bedrock calls and read-only billing checks. Never records credentials or prompts."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request
import uuid

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest
from botocore.config import Config

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / "artifacts"
LEDGER = ARTIFACTS / "invocations.jsonl"
PRINCIPAL_KEYS = {"iamPrincipal/owner", "iamPrincipal/product"}
MAX_CALLS_PER_DAY = 24
MAX_OUTPUT_TOKENS = 128
PROMPT = "This is a synthetic billing test. Reply with the single word OK."
CLIENT_CONFIG = Config(retries={"total_max_attempts": 1}, read_timeout=90, connect_timeout=10)


def now():
    return datetime.now(timezone.utc).isoformat()


def append_event(event, *, reserve=False):
    ARTIFACTS.mkdir(mode=0o700, exist_ok=True)
    with os.fdopen(os.open(LEDGER, os.O_RDWR | os.O_CREAT, 0o600), "r+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        if reserve:
            today = event["at"][:10]
            attempts = sum(
                row.get("event") == "attempt" and row["at"].startswith(today)
                for row in (json.loads(line) for line in f if line.strip())
            )
            if attempts >= MAX_CALLS_PER_DAY:
                raise RuntimeError("Daily request cap reached; preserve the ledger and retry another day.")
        f.seek(0, os.SEEK_END)
        f.write(json.dumps(event, sort_keys=True) + "\n")
        f.flush()
        os.fsync(f.fileno())


def assume(session, arn, name):
    credentials = session.client("sts", config=CLIENT_CONFIG).assume_role(
        RoleArn=arn, RoleSessionName=name, DurationSeconds=900
    )["Credentials"]
    return boto3.Session(
        aws_access_key_id=credentials["AccessKeyId"],
        aws_secret_access_key=credentials["SecretAccessKey"],
        aws_session_token=credentials["SessionToken"],
        region_name=session.region_name,
    )


def mantle(session, path, payload=None, project=None):
    region = session.region_name
    url = f"https://bedrock-mantle.{region}.api.aws/v1/{path}"
    headers = {"Content-Type": "application/json"}
    if project:
        headers["OpenAI-Project"] = project
    data = None if payload is None else json.dumps(payload).encode()
    request = AWSRequest(method="GET" if data is None else "POST", url=url, data=data, headers=headers)
    SigV4Auth(session.get_credentials().get_frozen_credentials(), "bedrock", region).add_auth(request)
    try:
        with urllib.request.urlopen(
            urllib.request.Request(url, data=data, headers=dict(request.headers)), timeout=90
        ) as response:
            return json.load(response), response.headers.get("x-amzn-requestid")
    except urllib.error.HTTPError as error:
        body = error.read().decode()
        raise RuntimeError(f"Mantle HTTP {error.code}: {body}") from None


def billing_tags(session):
    client = session.client("ce", region_name="us-east-1")
    return [
        tag
        for page in client.get_paginator("list_cost_allocation_tags").paginate()
        for tag in page["CostAllocationTags"]
    ]


def principal_tags_active(tags):
    return PRINCIPAL_KEYS <= {tag["TagKey"] for tag in tags if tag["Status"] == "Active"}


def check(session, config):
    member = assume(
        session,
        f"arn:aws:iam::{config['member_account_id']}:role/{config['member_access_role']}",
        "cost-lab-readonly",
    )
    iam = member.client("iam")
    for key, caller in config["callers"].items():
        name = caller["arn"].rsplit("/", 1)[-1]
        tags = {t["Key"]: t["Value"] for t in iam.list_role_tags(RoleName=name)["Tags"]}
        if tags != caller["tags"]:
            raise RuntimeError(f"{name}: unexpected role tags {tags}")
        resources = [
            f"arn:aws:bedrock:{config['region']}::foundation-model/{config['runtime_model_id']}",
            f"arn:aws:bedrock:{config['region']}::foundation-model/openai.gpt-oss-120b-1:0",
        ]
        result = iam.simulate_principal_policy(
            PolicySourceArn=caller["arn"],
            ActionNames=["bedrock:InvokeModel"],
            ResourceArns=resources,
        )["EvaluationResults"]
        decisions = {
            resource["EvalResourceName"]: resource["EvalResourceDecision"]
            for evaluation in result
            for resource in evaluation["ResourceSpecificResults"]
        }
        if decisions.get(resources[0]) != "allowed" or decisions.get(resources[1]) == "allowed":
            raise RuntimeError(f"{name}: unexpected Runtime permissions {decisions}")
        for model, expected in [(config["mantle_model_id"], "allowed"), ("openai.gpt-oss-120b", "implicitDeny")]:
            result = iam.simulate_principal_policy(
                PolicySourceArn=caller["arn"],
                ActionNames=["bedrock-mantle:CreateInference"],
                ResourceArns=[f"arn:aws:bedrock-mantle:{config['region']}:{config['member_account_id']}:project/default"],
                ContextEntries=[{
                    "ContextKeyName": "bedrock-mantle:Model",
                    "ContextKeyValues": [model],
                    "ContextKeyType": "string",
                }],
            )["EvaluationResults"]
            if result[0]["EvalDecision"] != expected:
                raise RuntimeError(f"{name}: unexpected Mantle permissions for {model}")
        print(f"{key}: tags and allowed/denied models verified")
    models, _ = mantle(member, "models")
    model = next(m for m in models["data"] if m["id"] == config["mantle_model_id"])
    print("Mantle model metadata:", json.dumps(model))


def invoke(session, config, args):
    if args.phase == "acceptance":
        if args.endpoint == "both":
            raise RuntimeError("Use separate UTC billing hours for Runtime and Mantle acceptance.")
        try:
            active = principal_tags_active(billing_tags(session))
        except Exception:
            if not args.console_activation_confirmed:
                raise
            active = False
        if not active and not args.console_activation_confirmed:
            raise RuntimeError("Activate IAM-principal tags first; discovery calls remain available.")
    failures = 0
    run_id = uuid.uuid4().hex[:12]
    for key, caller in sorted(config["callers"].items()):
        role_session = assume(session, caller["arn"], f"cost-lab-{args.phase}-{run_id}")
        identity = role_session.client("sts", config=CLIENT_CONFIG).get_caller_identity()
        if identity["Account"] != config["member_account_id"]:
            raise RuntimeError("Wrong invocation account.")
        endpoints = ["runtime", "mantle"] if args.endpoint == "both" else [args.endpoint]
        for endpoint in endpoints:
            event = {
                "event": "attempt", "at": now(), "id": uuid.uuid4().hex,
                "run_id": run_id, "phase": args.phase, "caller": key,
                "caller_arn": identity["Arn"], "tags": caller["tags"],
                "region": config["region"], "endpoint": endpoint,
                "model": config[f"{endpoint}_model_id"],
                "max_output_tokens": MAX_OUTPUT_TOKENS,
                "console_activation_confirmed": args.console_activation_confirmed,
            }
            append_event(event, reserve=True)
            try:
                if endpoint == "runtime":
                    response = role_session.client("bedrock-runtime", config=CLIENT_CONFIG).converse(
                        modelId=config["runtime_model_id"],
                        messages=[{"role": "user", "content": [{"text": PROMPT}]}],
                        inferenceConfig={"maxTokens": MAX_OUTPUT_TOKENS},
                    )
                    usage = response["usage"]
                    request_id = response["ResponseMetadata"]["RequestId"]
                    status = response["stopReason"]
                else:
                    response, request_id = mantle(
                        role_session, "responses",
                        {
                            "model": config["mantle_model_id"], "input": PROMPT,
                            "max_output_tokens": MAX_OUTPUT_TOKENS, "store": False,
                            "reasoning": {"effort": "low"},
                        },
                        config["mantle_project_id"],
                    )
                    usage = response["usage"]
                    status = response["status"]
                    request_id = request_id or response["id"]
                result = {
                    **event, "event": "result", "finished_at": now(),
                    "ok": True, "request_id": request_id, "usage": usage, "status": status,
                }
                print(f"{key}/{endpoint}: success; {json.dumps(usage)}")
            except Exception as error:
                failures += 1
                result = {**event, "event": "result", "finished_at": now(), "ok": False, "error": str(error)}
                print(f"{key}/{endpoint}: {error}", file=sys.stderr)
            append_event(result)
    if failures:
        raise RuntimeError(f"{failures} inference calls failed; see artifacts/invocations.jsonl")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", default="personal-administrator")
    parser.add_argument("--config", type=Path, default=ARTIFACTS / "test-config.json")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("check")
    sub.add_parser("tags")
    call = sub.add_parser("invoke")
    call.add_argument("--phase", choices=["discovery", "acceptance"], required=True)
    call.add_argument("--endpoint", choices=["runtime", "mantle", "both"], default="both")
    call.add_argument("--console-activation-confirmed", action="store_true",
                      help="Use only after confirming both IAM-principal tags are Active in the payer console.")
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    session = boto3.Session(profile_name=args.profile, region_name=config["region"])
    if session.client("sts").get_caller_identity()["Account"] != config["management_account_id"]:
        parser.error("The source profile is not the configured management account.")
    if args.command == "check":
        check(session, config)
    elif args.command == "tags":
        tags = billing_tags(session)
        print(json.dumps(tags, indent=2, default=str))
        print("Both IAM-principal tags active:", principal_tags_active(tags))
    else:
        invoke(session, config, args)


if __name__ == "__main__":
    main()
