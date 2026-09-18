# Member-account console access — 18 September 2026

The `account-access/` Terraform root created the missing member-account
`Administrator` role, attached `AdministratorAccess`, and granted the existing
management-account IAM user `vl.iliakov` permission to assume that role and
`OrganizationAccountAccessRole`.

The new user policy contains only `sts:AssumeRole` and the two member-account
role ARNs. The new `Administrator` trusts only the source IAM user.
The existing organization access role and its trust policy were preserved.

At **13:18:58 UTC**, direct `AssumeRole` calls from the `personal` profile
(`vl.iliakov`) succeeded for both roles. Read-only IAM calls using each resulting
session confirmed the destination account and the administrator policy attachment.
The post-apply Terraform plan reported no changes.

In the AWS Console, sign in as the management-account IAM user, choose
**Switch role**, and enter member account **874477031530** with either
**OrganizationAccountAccessRole** or **Administrator**. Root sessions cannot
switch roles.

Account access has a separate Terraform state from the disposable Bedrock lab.
