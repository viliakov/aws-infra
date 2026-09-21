# Session-tag experiment — 21 September 2026

Status: inference succeeded; billing validation is pending CUR delivery.

Terraform added the untagged `bedrock-cost-lab-session` IAM role and its
model-scoped inline policy in the existing member account. The reviewed plan
had two additions, no changes, and no destroys. The post-apply plan showed no
drift. Existing role-tag callers and billing activation were preserved.

The new role accepts only the test's `owner`/`product` keys and synthetic values.
Tags are supplied directly in STS `AssumeRole` requests. The test does not
provision an identity provider or evaluate tag propagation.

## Invocation evidence

- Run: `a00969c24622`.
- UTC invocation interval: 2026-09-21 08:44:13–08:44:20.
- CUR interval: `[2026-09-21T08:00:00Z, 2026-09-21T09:00:00Z)`.
- Eight successful calls; 648 input tokens and 375 output tokens in total.
- Maximum output tokens per request: 128; the existing 24-attempt daily cap
  remained enforced.

| Case | Role | Session `owner` / `product` | Runtime input/output | Mantle input/output |
| --- | --- | --- | --- | --- |
| `session-alice` | Shared, no static tags | `lab-session-alice` / `lab-session-product-a` | 81 / 63 | 81 / 22 |
| `session-bob` | Shared, no static tags | `lab-session-bob` / `lab-session-product-b` | 81 / 128 | 81 / 15 |
| `session-untagged` | Shared, no static tags | Absent | 81 / 59 | 81 / 14 |
| `static-control` | Original Alice role, static `lab-alice` / `lab-product-a` | Absent | 81 / 41 | 81 / 33 |

Each case/endpoint used a distinct session ARN, preserved with request IDs,
submitted tags, and usage in the ignored `artifacts/invocations.jsonl`.
Live IAM checks confirmed the shared role has no static tags and retained the
allowed/denied model restrictions.

CloudTrail independently recorded all eight successful assumptions. Both Alice
sessions carried `owner=lab-session-alice` and `product=lab-session-product-a`;
both Bob sessions carried the corresponding Bob/product-B values. Untagged and
static-control assumptions had no request tags. Sanitized events are preserved
in ignored `artifacts/session-assume-audit.json`.

## Billing result

Both `iamPrincipal/owner` and `iamPrincipal/product` were independently confirmed
Active before this run, with activation timestamp 2026-09-18 10:07:49 UTC.

The first billing check found no rows in the test interval. The current export
object was last modified at 2026-09-21 05:24:07 UTC, before these calls.
Verification correctly returned exit code 2 with all eight sessions awaiting
delivered usage. This is not evidence for or against session-tag billing support.

Repeat the read-only check after AWS publishes the usage:

```bash
uv run scripts/report.py \
  --bucket bedrock-cost-reports-606355870348-eu-central-1 \
  --start 2026-09-21T08:00:00Z --end 2026-09-21T09:00:00Z \
  --verify-session-run a00969c24622 \
  > artifacts/session-billing-summary.json
```

Passing requires positive usage attributed to each exact session ARN, the
different session-only tag values for Alice and Bob, blank tags for the untagged
control, and correct static tags for the static control, on both endpoints.

If usage arrives with correct static tags but missing session tags, preserve the
rows and ask AWS to confirm supported behavior. Do not infer SSO billing support
from STS or inference success alone.

## Validation

Terraform formatting and validation passed. Ten Python tests passed, including
checks that blank session tags, a missing endpoint/control, a failed call, and an
unknown run cannot produce a passing session-attribution result.
Live negative STS checks also rejected an unapproved owner value and an extra
tag key, confirming the session-tag trust restrictions.
