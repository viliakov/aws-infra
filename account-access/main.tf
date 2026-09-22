terraform {
  required_version = ">= 1.16.3, < 2.0"
  backend "s3" {}
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.65.0"
    }
  }
}

variable "management_account_id" {
  type = string
}

variable "member_account_id" {
  type = string
  validation {
    condition = (
      can(regex("^[0-9]{12}$", var.member_account_id)) &&
      var.member_account_id != var.management_account_id
    )
    error_message = "Select a member account distinct from the management account."
  }
}

variable "member_access_role" {
  type    = string
  default = "OrganizationAccountAccessRole"
}

variable "console_user_name" {
  type    = string
  default = "vl.iliakov"
}

variable "region" {
  type    = string
  default = "eu-central-1"
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.management_account_id]
}

provider "aws" {
  alias               = "member"
  region              = var.region
  allowed_account_ids = [var.member_account_id]
  assume_role {
    role_arn     = "arn:aws:iam::${var.member_account_id}:role/${var.member_access_role}"
    session_name = "terraform-member-account-access"
  }
}

data "aws_iam_user" "console" {
  user_name = var.console_user_name
}

data "aws_iam_role" "organization_access" {
  provider = aws.member
  name     = var.member_access_role
}

data "aws_iam_policy_document" "administrator_trust" {
  statement {
    sid     = "TrustConsoleUser"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [data.aws_iam_user.console.arn]
    }
  }
}

resource "aws_iam_role" "administrator" {
  provider           = aws.member
  name               = "Administrator"
  description        = "Personal member-account administrator for console access"
  assume_role_policy = data.aws_iam_policy_document.administrator_trust.json
}

resource "aws_iam_role_policy_attachment" "administrator" {
  provider   = aws.member
  role       = aws_iam_role.administrator.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

data "aws_iam_policy_document" "assume_member_roles" {
  statement {
    sid     = "AssumeMemberAccountRoles"
    actions = ["sts:AssumeRole"]
    resources = [
      data.aws_iam_role.organization_access.arn,
      aws_iam_role.administrator.arn,
    ]
  }
}

resource "aws_iam_user_policy" "assume_member_roles" {
  name   = "assume-member-roles-${var.member_account_id}"
  user   = data.aws_iam_user.console.user_name
  policy = data.aws_iam_policy_document.assume_member_roles.json
}

output "console_access" {
  value = {
    source_user_arn = data.aws_iam_user.console.arn
    account_id      = var.member_account_id
    role_names = [
      data.aws_iam_role.organization_access.name,
      aws_iam_role.administrator.name,
    ]
  }
}
