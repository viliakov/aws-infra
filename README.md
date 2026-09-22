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
[Automatic SAML SSO attribute lab](sso-idp/README.md), using the Terraform-managed
sandbox Keycloak realm.

## Layout

| Directory | Managed resources |
| --- | --- |
| `bootstrap/` | Encrypted, versioned S3 state bucket and TLS policy |
| `account-access/` | Member-account Administrator role and the source user's grant to assume both admin roles |
| `billing/` | Private EU report bucket, hourly CUR 2.0 export, member-account budget |
| `bedrock-lab/` | Empty retirement root for removing the former STS test roles |
| `billing-tags/` | Two IAM-principal tag activations, only if exposed by the billing API |
| `sso-lab/` | Identity Center users, bounded permission set, attribute configuration, and account assignments |
| `sso-idp/` | Sandbox Keycloak verification and synthetic user inventory |
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
in the chosen member account. Administration uses that profile; inference uses
the individual `lab-alice`, `lab-bob` or `lab-untagged` SSO profile.

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
```

Complete the [sandbox SSO setup](sso-idp/README.md), including the personal
Identity Center external-IdP connection, then apply the SSO root:

```bash
terraform -chdir=sso-lab init -backend-config=local.backend.hcl
terraform -chdir=sso-lab plan -out=sso.tfplan
terraform -chdir=sso-lab apply sso.tfplan
mkdir -p artifacts
umask 077
terraform -chdir=sso-lab output -json sso_test_config > artifacts/test-config.json
```

The active test configuration now comes entirely from `sso-lab/`. Every root's
provider rejects unexpected account IDs. Billing also verifies organization
membership. Inspect ownership and explicitly import any existing unmanaged
resource before adopting it.

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

## SSO inference and reporting

Follow the [SSO runbook](sso-idp/README.md) to verify automatic SAML attributes,
log in normally, and test each user's AWS identity. Then make bounded calls:

```bash
AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-alice --user alice --endpoint runtime
```

Repeat for Bob and the untagged control. Run Mantle tests in a separate UTC hour
so reused SSO session names do not mix the two endpoint tests in billing reports.
The permission set grants only the allowlisted model on each endpoint.

```bash
uv run scripts/report.py --bucket YOUR_REPORT_BUCKET \
  --start YYYY-MM-DDTHH:00:00Z --end YYYY-MM-DDTHH:00:00Z
```

Inspect the exact SSO caller ARNs and `iamPrincipal/owner` / `iamPrincipal/product`
values. Successful SAML login or inference alone does not establish billing
attribution; wait for delivered CUR records.

## Historical STS experiments

The static-tag and direct-session caller roles are retired. Their declarations
have been removed, so applying the retirement root cannot recreate them.
`scripts/lab.py check` and `invoke` reject an SSO configuration rather than
silently running zero test cases. Use `scripts/sso_probe.py` for current tests.

Historical billing checks remain available with the archived configuration:

```bash
uv run scripts/report.py --bucket YOUR_REPORT_BUCKET \
  --config artifacts/legacy/test-config.json \
  --start 2026-09-21T08:00:00Z --end 2026-09-21T09:00:00Z \
  --verify-session-run a00969c24622
```

The legacy report and invocation ledger are retained. That direct-session
experiment passed its delivered billing checks before retirement; see the
[cleanup record](docs/cleanup-2026-09-22.md).

## Checks and cleanup

```bash
terraform fmt -check -recursive
uv run python -m unittest discover -s tests -v
```

Run `terraform validate` in each initialized root, then inspect plans. After
apply, run another plan to check for drift.

`bedrock-lab/` is an empty retirement root. On an older deployment, review and
apply its plan to remove only the five legacy caller roles and five inline
policies. Archive its old output first and export the new SSO configuration as
shown above. After cleanup, its plan should report no changes.

Retain `sso-lab/`, `account-access/`, `billing/`, `bootstrap/`, and `billing-tags/`
for ongoing SSO billing tests. Do not remove the Identity Center provisioned role,
organization access role, billing data or AWS service-linked roles as part of
this cleanup. The organization, accounts and Mantle default project remain.

## References

- [AWS IAM-principal cost allocation](https://docs.aws.amazon.com/awsaccountbilling/latest/aboutv2/iam-principal-cost-allocation.html)
- [Enable Cost Explorer](https://docs.aws.amazon.com/cost-management/latest/userguide/ce-enable.html)
- [CUR 2.0 table configuration](https://docs.aws.amazon.com/cur/latest/userguide/table-dictionary-cur2.html)
- [Bedrock Responses API](https://docs.aws.amazon.com/bedrock/latest/userguide/inference-responses-api.html)
- [Bedrock Projects](https://docs.aws.amazon.com/bedrock/latest/userguide/projects.html)
