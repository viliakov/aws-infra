"""Bounded Bedrock calls using an existing SSO login; never supplies session tags."""

import argparse
import json
import uuid

import boto3

import lab


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--user", choices=("alice", "bob", "untagged"), required=True)
    parser.add_argument("--endpoint", choices=("runtime", "mantle"), required=True)
    parser.add_argument("--identity-only", action="store_true")
    args = parser.parse_args()
    config = json.loads((lab.ARTIFACTS / "test-config.json").read_text())
    user = json.loads((lab.ROOT / "sso-idp/users.json").read_text())[args.user]
    session = boto3.Session(profile_name=args.profile, region_name=config["region"])
    identity = session.client("sts", config=lab.CLIENT_CONFIG).get_caller_identity()
    prefix = f"arn:aws:sts::{config['member_account_id']}:assumed-role/AWSReservedSSO_BedrockCostLab_"
    if not identity["Arn"].startswith(prefix) or identity["Arn"].rsplit("/", 1)[-1] != user["username"]:
        raise RuntimeError("The current SSO login is not the selected lab user/permission set.")
    print("Verified SSO caller:", identity["Arn"])
    if args.identity_only:
        return
    event = {
        "event": "attempt", "at": lab.now(), "id": uuid.uuid4().hex,
        "run_id": uuid.uuid4().hex[:12], "experiment": "saml-sso",
        "caller": args.user, "caller_arn": identity["Arn"],
        "expected_tags": user["attributes"], "endpoint": args.endpoint,
        "region": config["region"], "model": config[f"{args.endpoint}_model_id"],
        "max_output_tokens": lab.MAX_OUTPUT_TOKENS,
    }
    lab.append_event(event, reserve=True)
    try:
        if args.endpoint == "runtime":
            response = session.client("bedrock-runtime", config=lab.CLIENT_CONFIG).converse(
                modelId=config["runtime_model_id"],
                messages=[{"role": "user", "content": [{"text": lab.PROMPT}]}],
                inferenceConfig={"maxTokens": lab.MAX_OUTPUT_TOKENS},
            )
            usage = response["usage"]
            request_id = response["ResponseMetadata"]["RequestId"]
        else:
            response, request_id = lab.mantle(session, "responses", {
                "model": config["mantle_model_id"], "input": lab.PROMPT,
                "max_output_tokens": lab.MAX_OUTPUT_TOKENS, "store": False,
                "reasoning": {"effort": "low"},
            }, config["mantle_project_id"])
            usage = response["usage"]
            request_id = request_id or response["id"]
        lab.append_event({**event, "event": "result", "finished_at": lab.now(),
                          "ok": True, "request_id": request_id, "usage": usage})
        print(json.dumps({"run_id": event["run_id"], "usage": usage}))
    except Exception as error:
        lab.append_event({**event, "event": "result", "finished_at": lab.now(),
                          "ok": False, "error": str(error)})
        raise


if __name__ == "__main__":
    main()
