# Personal AWS infrastructure

A Terraform lab for verifying Amazon Bedrock costs by IAM caller, `owner`, and
`product`. It uses an existing AWS Organization: the management account owns billing
and state, and an existing member account runs the inference tests.

The experiment supports [StackVista/terraform-infra#126](https://github.com/StackVista/terraform-infra/issues/126).
Its synthetic tags are local test values, not a proposed company taxonomy.

[Deployment evidence and pending billing checks](docs/validation-2026-09-17.md).
[Verified IAM-principal activation](docs/activation-2026-09-18.md).
[Verified member-account console access](docs/console-access-2026-09-18.md).
[Session-tag experiment and pending billing verification](docs/session-tags-2026-09-21.md).

## Layout

| Directory | Managed resources |
| --- | --- |
| `bootstrap/` | Encrypted, versioned S3 state bucket and TLS policy |
| `account-access/` | Member-account Administrator role and the source user's grant to assume both admin roles |
| `billing/` | Private EU report bucket, hourly CUR 2.0 export, member-account budget |
| `bedrock-lab/` | Four role-tag callers and one shared session-tag caller, with model-scoped inference policies |
| `billing-tags/` | Two IAM-principal tag activations, only if exposed by the billing API |
| `scripts/` | Configuration, bounded inference tests, billing inspection |
| `tests/` | Request-budget and financial aggregation checks |

Organization and account creation/deletion are deliberately outside the state.
The lab uses the existing Mantle default project, which has no resource tags.
This makes IAM-principal attribution observable without a second tagging mechanism.

Storage and inference run in Frankfurt by default. Billing's global control-plane
API uses `us-east-1`; the report and state buckets remain in Europe.
The allowlist contains GPT-OSS 20B on Runtime and Mantle only. Mantle metadata is
checked before testing. Prompts are fixed synthetic text; Responses requests set
`store=false`, which is not a claim about every provider retention policy.

## Setup

Install the Terraform version in `.terraform-version`, AWS CLI v2, and
[uv](https://docs.astral.sh/uv/). Terraform provider versions and Python dependencies
are locked. Terraform downloads should be checked against HashiCorp's published
SHA256SUMS. No container or persistent AWS credential is required.

Use a management-account profile that can assume `OrganizationAccountAccessRole`
in the chosen member account. The test roles trust the management account's
`Administrator` role; change `invoker_role_name` if your profile uses another role.
The profile must resolve to that role when running inference.

```bash
uv sync --locked
uv run scripts/configure.py \
  --management-account-id YOUR_PAYER_ID \
  --member-account-id YOUR_MEMBER_ID
export AWS_PROFILE=personal-administrator
```

The configuration script verifies both accounts and writes ignored, private
`local.auto.tfvars.json` and `local.backend.hcl` files. The budget notification
address comes from the organization's management account email. Review those
inputs locally. The monthly budget defaults to $10 for **all member-account costs**,
avoiding gaps in provider/Marketplace service filters. It alerts at 50% and 100%;
it does not stop spending.

In the payer console, open **Billing → Cost Explorer → Launch Cost Explorer**.
AWS does not expose initial enablement through an API. If IAM billing access is
disabled, the account owner must enable it. Expect billing data to take time to
appear after enabling.

## Apply in stages

Review each saved plan before applying it. All AWS resource writes belong to
Terraform; do not repair the experiment with direct AWS create/update/delete calls.

On the first run, bootstrap starts with local state:

```bash
terraform -chdir=bootstrap init
terraform -chdir=bootstrap plan -out=bootstrap.tfplan
terraform -chdir=bootstrap apply bootstrap.tfplan
cp bootstrap/backend.tf.example bootstrap/backend.tf
terraform -chdir=bootstrap init -migrate-state -backend-config=local.backend.hcl
```

Accept Terraform's state-copy prompt after confirming the destination. This
migrates bootstrap state into the new S3 bucket. Keep the generated backend file
and local backup until migration is verified. On subsequent runs use `init`
with the same backend configuration; do not bootstrap a second state.

```bash
terraform -chdir=billing init -backend-config=local.backend.hcl
terraform -chdir=billing plan -out=billing.tfplan
terraform -chdir=billing apply billing.tfplan
terraform -chdir=bedrock-lab init -backend-config=local.backend.hcl
terraform -chdir=bedrock-lab plan -out=lab.tfplan
terraform -chdir=bedrock-lab apply lab.tfplan
mkdir -p artifacts
terraform -chdir=bedrock-lab output -json test_config > artifacts/test-config.json
uv run scripts/lab.py check
uv run scripts/lab.py invoke --phase discovery
```

Every root's provider rejects unexpected account IDs. Billing also verifies that
the existing organization contains the selected member account. If a planned
resource already exists outside this state, stop and explicitly import it after
reviewing its owner and configuration.

The discovery batch makes eight calls: one Runtime Converse call and one Mantle
Responses call for each role:

| Caller | `owner` | `product` |
| --- | --- | --- |
| `alice-a` | `lab-alice` | `lab-product-a` |
| `bob-a` | `lab-bob` | `lab-product-a` |
| `alice-b` | `lab-alice` | `lab-product-b` |
| `untagged` | absent | absent |

## Console access to the member account

The `account-access/` root grants the existing management-account IAM user
`vl.iliakov` permission to assume exactly these member-account roles:
`OrganizationAccountAccessRole` and `Administrator`. The latter is created with
the AWS-managed `AdministratorAccess` policy and trusts only that IAM user.
The existing organization access role is read as a data source.

After configuring inputs and bootstrapping state, apply:

```bash
terraform -chdir=account-access init -backend-config=local.backend.hcl
terraform -chdir=account-access plan -out=access.tfplan
terraform -chdir=account-access apply access.tfplan
```

If `Administrator` already exists in another account where this configuration
is reused, inspect its owner, trust and permissions, and prepare an explicit
import before applying. Override `--console-user-name` when preparing inputs
for another IAM user.

Sign in to the management account as the IAM user, then choose **Switch role**
from the account menu. Enter the member account ID and either role name above.
Root users cannot switch roles. The console authorizes against the original
signed-in identity, which is why this user grant is required even when the CLI
already works through the management `Administrator` role.

This root has its own state and survives cleanup of `bedrock-lab/`.

## Request limits

Each request permits at most 128 output tokens. The local ledger permits at most
24 attempted inference requests per UTC day, including failed and interrupted
requests. Automatic inference retries are disabled. Preserve
`artifacts/invocations.jsonl`; deleting it defeats the local guard. This is a
bounded test harness, not an account-wide hard spending limit.

## Activate IAM-principal tags

Tags become discoverable only after tagged callers invoke Bedrock. Allow up to
24 hours for discovery and another 24 hours for activation.

```bash
uv run scripts/lab.py tags
```

Resource `owner`/`product` activation does not activate IAM-principal tags.
The live API exposed the exact keys `iamPrincipal/owner` and
`iamPrincipal/product` on 18 September 2026. The activation root now declares
both by default. For a new account, wait until both keys are discoverable before
applying this root:

```bash
terraform -chdir=billing-tags init -backend-config=local.backend.hcl
terraform -chdir=billing-tags plan -out=activation.tfplan
terraform -chdir=billing-tags apply activation.tfplan
uv run scripts/lab.py tags
```

Independently re-read their status. The provider may not surface individual tag
errors returned inside an HTTP 200 response, so apply success alone is insufficient.
If activation already happened outside Terraform, import the exact verified keys
before managing them.

If another account's API does not expose those keys, activate **owner** and **product** in
**Billing → Cost allocation tags → IAM principal**. Record the UTC activation
time. Override `principal_tag_api_keys = []` before first applying that root;
do not activate similarly named Resource tags
as a substitute.

## Acceptance and reporting

After both IAM-principal tags are Active, run Runtime and Mantle acceptance in
**different UTC billing hours**, without discovery calls in those hours:

```bash
uv run scripts/lab.py invoke --phase acceptance --endpoint runtime
```

In a later UTC hour, run the same command with `--endpoint mantle`. CUR may
aggregate calls to the same model without a distinct endpoint label; separate
hours let us prove each endpoint's attribution independently.

If activation was verified in the console but its status is not exposed by the
API, add `--console-activation-confirmed`. Use this only after verifying both
tags, not to bypass propagation. The ledger records that assertion.

Cost Explorer can group the member-account costs by `iamPrincipal/owner` and
`iamPrincipal/product`. CUR 2.0 includes `line_item_iam_principal` and the tags
map because the export enables `INCLUDE_IAM_PRINCIPAL_DATA`.

The export includes all selected member-account line items, preserving untagged
usage, discounts, and provider billing variations. It overwrites each current
report; S3 versioning preserves older versions. Report against the current objects,
not every historical S3 version or a pile of old local downloads.

```bash
REPORT_BUCKET=$(terraform -chdir=billing output -raw reports_bucket)
uv run scripts/report.py --bucket "$REPORT_BUCKET" \
  --start 2026-09-17T12:00:00Z --end 2026-09-17T13:00:00Z \
  --verify-callers \
  > artifacts/billing-summary.json
```

Replace the example interval with the actual UTC acceptance hour and run once
per endpoint. `--verify-callers` fails until all four expected caller/tag
combinations have positive usage in that window. The script
reads compressed CSV from S3, preserves decimal cost precision, and keeps input
and output usage units separate. CUR is hourly aggregation: request IDs from
the local ledger are supporting evidence, not join keys in CUR.

Acceptance requires delivered billing data, not just successful requests:

- Each tagged caller appears with the intended principal owner and product.
- Both Runtime and Mantle usage are covered.
- The untagged control remains visible without fabricated owner/product values.
- The grouped costs reconcile with the corresponding account usage. Treat credits
  and taxes separately from usage charges.
- Principal attribution and resource attribution are alternative views of the
  same spend; never add their totals together.
- Preserve activation time, inference evidence, report interval, and any missing
  attribution. Do not promise retroactive coverage.

Allow several days for discovery, activation, and export delivery. No scheduled
inference loop is created.

## Session-tag billing experiment

The shared `bedrock-cost-lab-session` role has **no static tags**. Two sessions
receive synthetic `owner` and `product` values directly in the STS request;
a third receives no tags. A fourth caller uses the original statically tagged
Alice role as a control. This tests session-tag billing independently of an
identity provider. It does not provision SSO or test tag propagation.

Apply the updated `bedrock-lab/` plan and refresh its output first:

```bash
terraform -chdir=bedrock-lab output -json test_config > artifacts/test-config.json
uv run scripts/lab.py check
uv run scripts/lab.py invoke --phase acceptance --session-tags --endpoint both
```

| Case | Static role tags | Session tags |
| --- | --- | --- |
| `session-alice` | None | `owner=lab-session-alice`, `product=lab-session-product-a` |
| `session-bob` | None | `owner=lab-session-bob`, `product=lab-session-product-b` |
| `session-untagged` | None | None |
| `static-control` | `owner=lab-alice`, `product=lab-product-a` | None |

The run makes eight calls, four per endpoint, under the existing daily cap.
Every case/endpoint gets a unique session ARN recorded in the ledger, allowing
both endpoints to run in one billing hour. Both principal keys must already be
active. The role trust permits only the synthetic session tag keys and values.

Once CUR data arrives, use the printed run ID and its UTC billing-hour interval:

```bash
uv run scripts/report.py --bucket "$REPORT_BUCKET" \
  --start START_UTC_HOUR --end END_UTC_HOUR \
  --verify-session-run RUN_ID > artifacts/session-billing-summary.json
```

Verification requires all eight successful calls, each exact session ARN, and
the expected tags on positive billed usage. Missing rows return exit code 2;
they are not evidence that session tags are unsupported. If static control
attribution works but delivered session-only calls have blank principal tags,
record that difference and investigate it with AWS before promising SSO-based
billing. A successful STS or Bedrock call alone does not prove billing support.

## Checks and cleanup

```bash
terraform fmt -check -recursive
uv run python -m unittest discover -s tests -v
```

Run `terraform validate` in each initialized root, then inspect plans. After
apply, run another plan to check for drift.

Destroy only `bedrock-lab/` after the billing experiment. This removes its five
roles and inline policies. Retain `account-access/`, `billing/`, `bootstrap/`, and tag activation
state until the evidence is no longer needed. Buckets reject destruction in
Terraform and cannot be force-emptied. The organization, accounts, and default
Mantle project are never destroyed by this repository.

## References

- [AWS IAM-principal cost allocation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/iam-principal-cost-allocation.html)
- [Enable Cost Explorer](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-enable.html)
- [CUR 2.0 table configuration](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2.html)
- [Bedrock Responses API](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-responses-api.html)
- [Bedrock Projects](https://docs.aws.amazon.com/bedrock/latest/userguide/projects.html)
