# Personal lab validation — 17 September 2026

Point-in-time evidence from deployment and discovery, not final billing acceptance.

## Applied

Terraform created 22 resources across the bootstrap, billing, and inference roots:
the two protected EU buckets and their configuration, the CUR 2.0 export, a
$10 monthly member-account budget, and four IAM roles with inline policies.
No existing organization, account, or workload was replaced or deleted.
The bootstrap state was migrated to S3; bootstrap, billing, and inference state
objects were verified there.

All four roots validated. Post-apply plans exited with no changes.
The billing-tags root remains intentionally empty until the principal keys
become discoverable and their API representation can be verified.

The export was read back with `INCLUDE_IAM_PRINCIPAL_DATA=TRUE`, hourly
granularity, the selected member-account filter, and a Frankfurt destination.
Its status was `HEALTHY`; there were no executions or report objects yet.

AWS's delivery validation writes `aws-programmatic-access-test-object` at the
bucket root. A prefix-only write grant failed export creation. The final policy
uses the documented bucket-wide `PutObject` grant for the Data Exports service,
restricted to the payer's export ARNs and source account. The bucket is dedicated
to reports, blocks public access, requires TLS, and grants no object read/delete
permission to the export service.

## Inference and identity

Discovery ran from **09:41:01 to 09:41:08 UTC** in Frankfurt. All eight requests
succeeded using fresh assumed-role credentials:

| Caller | Runtime input/output tokens | Mantle input/output tokens |
| --- | --- | --- |
| `alice-a` | 81 / 57 | 81 / 22 |
| `alice-b` | 81 / 55 | 81 / 18 |
| `bob-a` | 81 / 85 | 81 / 15 |
| `untagged` | 81 / 34 | 81 / 20 |

These are response usage counters, not billed costs. Runtime used
`openai.gpt-oss-20b-1:0`; Mantle used `openai.gpt-oss-20b` and the existing untagged
default project. Only fixed synthetic prompts were sent.

Live role tags matched the test matrix, including the untagged control. IAM
simulation allowed the selected model and denied a different model for both
endpoints under all four roles. Actual inference verified the positive access
path. No inference call used administrator credentials directly.

The local ignored invocation ledger retains UTC timestamps, caller ARNs, request
IDs, endpoint/model identifiers, and usage counts. It does not contain credentials,
prompts, or response text.

## Billing still pending

The owner opened Cost Explorer and received AWS's data-preparation notice.
Afterwards `ListCostAllocationTags` became accessible, but returned only
`aws:createdBy`; neither principal key was discoverable yet.

Next steps:

1. Recheck principal-tag discovery after AWS propagation.
2. Activate **IAM principal** `owner` and `product`, recording the UTC time.
   Use Terraform only if the API exposes the exact prefixed keys; otherwise
   use the documented payer-console step.
3. Confirm both tags are Active. Run Runtime and Mantle acceptance in different
   UTC billing hours, using the commands in the README.
4. Wait for report delivery and validate each endpoint's hour with
   `scripts/report.py --verify-callers`.
5. Reconcile tagged and untagged usage against billed account usage without
   adding resource attribution as another expense.

No historical attribution, final billed-cost reconciliation, or tag activation
has been claimed. No recurring inference schedule was installed.
