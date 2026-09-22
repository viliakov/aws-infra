# IAM-principal activation — 18 September 2026

Terraform activated `iamPrincipal/owner` and `iamPrincipal/product` in the
management account at **10:07:49 UTC (12:07:49 Amsterdam time)**.
An independent `ListCostAllocationTags` request returned `Active` for both,
with that `LastUpdatedDate`. The post-apply Terraform plan reported no changes.
Other cost-allocation tags were not activated.

Before activation, the first delivered CUR report identified all four caller
roles and both inference endpoints. Its discovery-hour usage cost reconciled
with Cost Explorer at $0.00018072. Principal owner/product values were empty,
consistent with the tags being inactive when checked.

The API representation is now proven: Terraform can manage these principal
tags directly using the prefixed keys. Console activation is unnecessary for
this account.

Next: run the post-activation acceptance calls and inspect subsequently delivered
reports for the expected owner/product combinations. Activation alone does not
prove billed attribution or historical backfill.
