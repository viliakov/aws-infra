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
}

variable "region" {
  type    = string
  default = "eu-central-1"
  validation {
    condition     = startswith(var.region, "eu-")
    error_message = "Keep this lab in a European region."
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.management_account_id]
}

data "aws_ssoadmin_instances" "lab" {}

locals {
  instance_arn      = try(one(data.aws_ssoadmin_instances.lab.arns), null)
  identity_store_id = try(one(data.aws_ssoadmin_instances.lab.identity_store_ids), null)
  ready             = local.instance_arn != null && local.identity_store_id != null
  users             = jsondecode(file("${path.module}/../sso-idp/users.json"))
  runtime_model_id  = "openai.gpt-oss-20b-1:0"
  mantle_model_id   = "openai.gpt-oss-20b"
  mantle_project_id = "default"
}

resource "aws_identitystore_user" "lab" {
  for_each          = local.ready ? local.users : {}
  identity_store_id = local.identity_store_id
  user_name         = each.value.username
  display_name      = "${each.value.given_name} ${each.value.family_name}"

  name {
    given_name  = each.value.given_name
    family_name = each.value.family_name
  }

  emails {
    value   = each.value.username
    primary = true
    type    = "work"
  }
}

resource "aws_ssoadmin_permission_set" "bedrock" {
  count            = local.ready ? 1 : 0
  name             = "BedrockCostLab"
  description      = "Synthetic SAML attribute and bounded Bedrock billing tests"
  instance_arn     = local.instance_arn
  session_duration = "PT1H"
}

resource "aws_ssoadmin_instance_access_control_attributes" "lab" {
  count        = local.ready ? 1 : 0
  instance_arn = local.instance_arn

  # Enable ABAC without overriding owner/product supplied by the SAML provider.
  attribute {
    key = "labSubject"
    value {
      source = ["$${path:userName}"]
    }
  }
}

data "aws_iam_policy_document" "inference" {
  statement {
    sid       = "RuntimeModel"
    actions   = ["bedrock:InvokeModel"]
    resources = ["arn:aws:bedrock:${var.region}::foundation-model/${local.runtime_model_id}"]
  }

  statement {
    sid       = "MantleModel"
    actions   = ["bedrock-mantle:CreateInference"]
    resources = ["arn:aws:bedrock-mantle:${var.region}:${var.member_account_id}:project/${local.mantle_project_id}"]
    condition {
      test     = "StringEquals"
      variable = "bedrock-mantle:Model"
      values   = [local.mantle_model_id]
    }
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "bedrock" {
  count              = local.ready ? 1 : 0
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.bedrock[0].arn
  inline_policy      = data.aws_iam_policy_document.inference.json
}

resource "aws_ssoadmin_account_assignment" "lab" {
  for_each           = aws_identitystore_user.lab
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.bedrock[0].arn
  principal_id       = each.value.user_id
  principal_type     = "USER"
  target_id          = var.member_account_id
  target_type        = "AWS_ACCOUNT"

  depends_on = [
    aws_ssoadmin_permission_set_inline_policy.bedrock,
    aws_ssoadmin_instance_access_control_attributes.lab,
  ]
}

output "sso_test_config" {
  value = {
    management_account_id = var.management_account_id
    runtime_model_id      = local.runtime_model_id
    mantle_model_id       = local.mantle_model_id
    mantle_project_id     = local.mantle_project_id
    instance_arn          = local.instance_arn
    identity_store_id     = local.identity_store_id
    member_account_id     = var.member_account_id
    region                = var.region
    permission_set        = try(aws_ssoadmin_permission_set.bedrock[0].name, null)
    users                 = local.users
  }

  precondition {
    condition     = local.ready
    error_message = "Enable an organization instance of IAM Identity Center in the personal management account and selected region through the AWS console first."
  }
}
