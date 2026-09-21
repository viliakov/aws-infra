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
- `../scripts/sso_idp.py` prepares and runs local Keycloak, configures users and
  SAML mappings, verifies signed login responses, and generates isolated CLI
  profiles.
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

## Start and verify the local identity provider

Run from the repository root:

```bash
uv sync --locked
uv run scripts/sso_idp.py prepare
uv run scripts/sso_idp.py build
uv run scripts/sso_idp.py start
```

Allow Keycloak startup to finish, then run:

```bash
uv run scripts/sso_idp.py configure
uv run scripts/sso_idp.py verify-logins
```

The test performs actual password logins for all three users. It checks the
signed response against the metadata certificate, verifies issuer, audience,
NameID and attribute values, and saves only the non-secret result. It does not
save SAML assertions or tokens.

The IdP runs at `https://localhost:8843`, published on the host's loopback address
only. It uses a generated localhost TLS certificate. For browser testing, trust
this lab certificate or accept its local certificate warning. The scripts
validate TLS against the generated certificate; verification is not disabled.
Keep Keycloak running while using the AWS login flow.

Private local files are under ignored `artifacts/sso-idp/`:

- `credentials.json`: generated administrator and test-user passwords.
- `idp-metadata.xml`: the IdP metadata to upload to AWS.
- `tls.crt` / `tls.key`: localhost TLS certificate and private key.
- `data/`: persistent Keycloak database, including its signing keys.
- `login-verification.json`: sanitized local login evidence.

The Docker image uses a digest-pinned SUSE BCI OpenJDK base. The Keycloak archive
is verified against the SHA256 digest published with its upstream release.
The current base digest targets Linux amd64. This is a local development IdP,
with a file-backed database and a 2 GiB container memory limit.

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

Connect Keycloak to the AWS service provider:

```bash
uv run scripts/sso_idp.py configure \
  --sp-metadata artifacts/sso-idp/aws-sp-metadata.xml
uv run scripts/sso_idp.py profiles --start-url YOUR_AWS_ACCESS_PORTAL_URL
```

This configures the AWS SAML client using the actual AWS entity ID and assertion
consumer URL. The local preview client is only for assertion testing.

## Apply AWS permissions through Terraform

`prepare` generates the new root's ignored inputs and its own backend key using
the existing lab configuration. It does not change the other roots' inputs.

```bash
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab init \
  -backend-config=local.backend.hcl
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab plan -out=sso.tfplan
AWS_PROFILE=personal-administrator terraform -chdir=sso-lab apply sso.tfplan
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
not silently select the previous user. Use the generated credentials locally.

```bash
AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  aws sso login --profile lab-alice --use-device-code --no-browser

AWS_CONFIG_FILE="$PWD/artifacts/sso-idp/aws-config" \
  uv run scripts/sso_probe.py --profile lab-alice --user alice \
  --endpoint runtime --identity-only
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

Use `scripts/report.py` for each interval and inspect the users' exact caller
ARNs and `iamPrincipal/owner` / `iamPrincipal/product` values. The existing
`--verify-session-run` option applies to the earlier direct STS experiment, not
these SSO logins. Successful login and inference do not prove billed attribution;
wait for delivered CUR data.

## Stopping and cleanup

Stop the local IdP without deleting its database:

```bash
docker --config .tools/docker stop bedrock-cost-lab-idp
```

Restart it with `uv run scripts/sso_idp.py start`. Destroying `sso-lab/` removes
only that root's users, assignments, permission set/policy and attribute
configuration. The console-created Identity Center instance and its external
IdP connection remain for explicit cleanup after testing.

## References

- [AWS external identity providers](https://docs.aws.amazon.com/singlesignon/latest/userguide/manage-your-identity-source-idp.html)
- [AWS CreateInstance limitations](https://docs.aws.amazon.com/singlesignon/latest/APIReference/API_CreateInstance.html)
- [Enable IAM Identity Center](https://docs.aws.amazon.com/singlesignon/latest/userguide/enable-identity-center.html)
- [Change the identity source](https://docs.aws.amazon.com/singlesignon/latest/userguide/manage-your-identity-source-change.html)
- [Enable attributes for access control](https://docs.aws.amazon.com/singlesignon/latest/userguide/configure-abac.html)
- [SAML attribute mappings](https://docs.aws.amazon.com/singlesignon/latest/userguide/attributesforaccesscontrol.html)
- [Keycloak container setup](https://www.keycloak.org/server/containers)
- [Keycloak release and published archive digest](https://github.com/keycloak/keycloak/releases/tag/26.7.4)
