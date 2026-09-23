# SAML SSO attribute lab

This reproduces an external identity provider → IAM Identity Center → AWS account
login. Keycloak holds the synthetic users and their attributes. Users log in
normally; the IdP adds attributes automatically to signed SAML responses.

The AWS permission set grants only the existing GPT-OSS 20B Runtime and Mantle
test access in the member account. All three users share that permission set.

## Prepared components

- `users.json` is the shared synthetic user inventory. Terraform provisions
  matching Identity Center user records; Keycloak holds passwords and the
  authoritative `owner`/`product` attributes.
- `../scripts/sso_idp.py` downloads sandbox metadata, verifies signed login
  responses, and generates isolated CLI profiles. Realm administration belongs
  to `terraform-infra/keycloak/sandbox-main`.
- `../sso-lab/` owns the Identity Center users, permission set, model-scoped
  policy, attribute configuration, and member-account assignments in Terraform.
- `../scripts/sso_probe.py` makes bounded inference calls with an existing SSO
  login. It does not assume an additional role or supply attribution values.

| User | SAML `owner` | SAML `product` |
| --- | --- | --- |
| Alice | `lab-sso-alice` | `lab-sso-product-a` |
| Bob | `lab-sso-bob` | `lab-sso-product-b` |
| Untagged control | Absent | Absent |

Both attributes are editable only by Keycloak administrators. The SAML names are
`https://aws.amazon.com/SAML/Attributes/AccessControl:owner` and
`https://aws.amazon.com/SAML/Attributes/AccessControl:product`. NameID is the user's
synthetic email and matches the Identity Center username.

Terraform enables Identity Center attributes for access control with a
`labSubject` mapping to the directory username. The AWS provider requires at least
one directory mapping. Keeping that key distinct preserves `owner`/`product`
from the SAML assertion. `labSubject` is not activated for cost allocation.
The control user has no owner/product attribution, although it has `labSubject`.

## Prepare sandbox Keycloak metadata

The active IdP is the separate `bedrock-cost-lab` realm at
`https://sso.sandbox-main.sandbox.stackstate.io`. Its users, attributes, SAML
clients and permissions are managed through Terraform in
[terraform-infra](https://github.com/StackVista/terraform-infra/pull/127).
The helper uses normal HTTPS certificate validation and only submits login forms.
It has no Docker commands or Keycloak administration calls.

First complete the repository's `scripts/configure.py` setup to generate the
SSO Terraform inputs and backend settings. Obtain the encrypted password file
from the applied Terraform revision, then run from this repository root with
credentials allowed to decrypt its SOPS/KMS key:

```bash
uv sync --locked
AWS_PROFILE=stackstate-infosec uv run scripts/sso_idp.py prepare \
  --passwords-sops /path/to/terraform-infra/keycloak/sandbox-main/secrets/sops.bedrock_cost_lab_passwords.json
uv run scripts/sso_idp.py configure
```

`prepare` checks those inputs and stores sandbox passwords in an ignored,
mode-600 file. It does not generate new passwords or reuse the old Docker
credentials. Once prepared, subsequent verification needs no KMS credentials.

`configure` downloads the realm's public metadata to
`artifacts/sso-idp/idp-metadata.xml`; it does not modify Keycloak.
**This download does not require AWS SP metadata.** Obtain that second file
in the console step below, then verify logins.

The following steps store local files under ignored `artifacts/sso-idp/`:

- `sandbox-passwords.json`: the three sandbox passwords, keyed by user name.
- `idp-metadata.xml`: sandbox metadata to upload to AWS.
- `idp-metadata.previous.xml`: previous issuer metadata preserved during migration.
- `aws-sp-metadata.xml`: the personal AWS lab's service-provider metadata.
- `aws-config`: isolated AWS CLI profiles; the portal URL is unchanged by migration.
- `login-verification.json`: sanitized sandbox login evidence.

The sandbox issuer and signing certificate differ from the Docker IdP. In the
personal lab's IAM Identity Center, replace the external IdP metadata with the
new `idp-metadata.xml` before testing normal AWS login. Use the sandbox passwords
for these logins. The old `credentials.json`, TLS files and Docker database are
not used by the helper.

## Required AWS console setup

AWS does not expose creation of an **organization** Identity Center instance
through `CreateInstance`; that API rejects management-account requests.
Changing the identity source to an external IdP is also a console setup step.
The Terraform root reads the instance and fails its plan clearly until it exists.

In the **personal management account**, select **Frankfurt (`eu-central-1`)**:

1. Open **IAM Identity Center** and enable an **organization instance**. Choose
   a single-region instance with multi-account permissions and the default
   AWS-managed encryption key.
2. Open **Settings → Identity source → Actions → Change identity source**.
   Select **External identity provider**.
3. Download the **AWS service provider metadata** to
   `artifacts/sso-idp/aws-sp-metadata.xml`.
4. Upload `artifacts/sso-idp/idp-metadata.xml` as the identity provider metadata,
   review the settings, and complete the change.
5. Record the **AWS access portal URL** shown by Identity Center.

No passwords or private signing keys need to be uploaded to AWS.

## Verify sandbox SAML responses and prepare CLI profiles

At this point, both metadata files must exist: `idp-metadata.xml` was downloaded
from Keycloak, and `aws-sp-metadata.xml` was downloaded from AWS.

The sandbox AWS client is managed by Terraform. Its entity ID and
assertion-consumer (ACS) URLs must match the downloaded AWS metadata. For a new
Identity Center instance, or changed URLs, update and apply the owning
`terraform-infra/keycloak/sandbox-main` configuration through its reviewed
workflow before continuing. The helper does not configure that client.

If you saved the AWS download elsewhere, import it first:

```bash
uv run scripts/sso_idp.py configure \
  --sp-metadata /path/to/downloaded/aws-sp-metadata.xml
```

This optional argument validates the file and copies it to
`artifacts/sso-idp/aws-sp-metadata.xml`. Skip this import if you already saved it
there. The command also refreshes the independent Keycloak metadata download.

Now verify the SAML responses and generate profiles using the recorded portal URL:

```bash
uv run scripts/sso_idp.py verify-logins
uv run scripts/sso_idp.py profiles --start-url YOUR_AWS_ACCESS_PORTAL_URL
```

`verify-logins` performs password logins for all three users. It verifies signed
issuer, destination, audience, NameID, assertion lifetime and exact attributes
against the metadata. Only the real AWS client is checked (three logins).
The verifier captures SAML forms without submitting them to AWS, so this does
not establish an AWS session or prove billing.

The profile generator accepts both legacy `https://your-company.awsapps.com/start`
URLs and dual-stack `https://ssoins-INSTANCE.portal.REGION.app.aws` URLs.
Use the portal URL displayed by your instance without changing its hostname.

## Apply AWS permissions through Terraform

`scripts/configure.py` generates this root's ignored inputs and backend settings.
The retired `bedrock-lab/` root is not a dependency of SSO setup or verification.

```bash
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab init \
  -backend-config=local.backend.hcl
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab plan -out=sso.tfplan
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab apply sso.tfplan
umask 077
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab output -json sso_test_config \
  > artifacts/test-config.json
```

Review the plan before applying. Expected additions are three users, one
permission set, its inline policy, one attribute configuration, and three account
assignments. The organization, accounts, and Identity Center instance are not
created or destroyed by this root. Identity Center provisions the corresponding
account IAM role and SAML provider itself.

If the attribute configuration or any matching user is already managed outside
this root, inspect it and prepare an import rather than replacing it.

## Validate normal SSO login

Open a separate private browser session for each user so an existing login does
not silently select the previous user. Use the sandbox passwords stored locally.

```bash
AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-alice --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-alice --user alice \
  --endpoint runtime --identity-only

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-bob --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-bob --user bob \
  --endpoint runtime --identity-only

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-untagged --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-untagged --user untagged \
  --endpoint runtime --identity-only

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-alice --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-alice --user alice \
  --endpoint mantle --identity-only

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-bob --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-bob --user bob \
  --endpoint mantle --identity-only

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-untagged --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-untagged --user untagged \
  --endpoint mantle --identity-only

```

Repeat for `lab-bob`/`bob` and `lab-untagged`/`untagged`. These commands use a
separate configuration file and do not overwrite personal or company profiles.

Verify AWS CloudTrail `AssumeRoleWithSAML` events for the generated
`AWSReservedSSO_BedrockCostLab_*` role. The expected principal tags are the
table's values plus `labSubject`. This confirms AWS received the automatic
attributes, beyond the local signed-assertion check.

For billing tests, omit `--identity-only`. Run the three Runtime calls in one
UTC billing hour and the three Mantle calls in a later hour. The SSO session
name can be reused across requests, so separate hours make endpoint attribution
unambiguous. The shared daily ledger still caps all lab inference attempts at
24, with at most 128 output tokens per call.

```bash
AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-alice --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-alice --user alice \
  --endpoint runtime

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-bob --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-bob --user bob \
  --endpoint runtime

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-untagged --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-untagged --user untagged \
  --endpoint runtime

# Mantle
AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-alice --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-alice --user alice \
  --endpoint mantle

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-bob --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-bob --user bob \
  --endpoint mantle

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-untagged --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-untagged --user untagged \
  --endpoint mantle

```

Use `scripts/report.py` for each interval and inspect the users' exact caller
ARNs and `iamPrincipal/owner` / `iamPrincipal/product` values. The existing
`--verify-session-run` option applies to the earlier direct STS experiment, not
these SSO logins. Successful login and inference do not prove billed attribution;
wait for delivered CUR data.

For example,
```
uv run scripts/report.py --bucket aws-infra-state-606355870348-eu-central-1 \
  --start 2026-09-22T14:00:00Z --end 2026-09-22T15:00:00Z

uv run scripts/report.py --bucket aws-infra-state-606355870348-eu-central-1 \
  --start 2026-09-22T15:00:00Z --end 2026-09-22T16:00:00Z
```

## Cleanup

Once AWS login uses sandbox Keycloak, the previous Docker IdP can be stopped:

```bash
docker --config .tools/docker stop bedrock-cost-lab-idp
```

Deleting this personal lab's `sso-lab/` resources does not delete the shared
Keycloak realm. Remove that realm through its owning Terraform project and
reviewed Atlantis workflow. Its runbook includes the required user-profile
removal step for the pinned provider. The console-created Identity Center
instance and its external IdP connection need separate explicit cleanup.

## References

- [AWS external identity providers](https://docs.aws.amazon.com/singlesignon/latest/userguide/manage-your-identity-source-idp.html)
- [AWS CreateInstance limitations](https://docs.aws.amazon.com/singlesignon/latest/APIReference/API_CreateInstance.html)
- [Enable IAM Identity Center](https://docs.aws.amazon.com/singlesignon/latest/userguide/enable-identity-center.html)
- [Change the identity source](https://docs.aws.amazon.com/singlesignon/latest/userguide/manage-your-identity-source-change.html)
- [Enable attributes for access control](https://docs.aws.amazon.com/singlesignon/latest/userguide/configure-abac.html)
- [SAML attribute mappings](https://docs.aws.amazon.com/singlesignon/latest/userguide/attributesforaccesscontrol.html)
- [AWS CLI SSO configuration and dual-stack portal URLs](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html)
