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

variable "member_access_role" {
  type    = string
  default = "OrganizationAccountAccessRole"
}

variable "invoker_role_name" {
  type    = string
  default = "Administrator"
}

variable "region" {
  type    = string
  default = "eu-central-1"
  validation {
    condition     = startswith(var.region, "eu-")
    error_message = "Run this lab in a European region."
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.member_account_id]
  assume_role {
    role_arn     = "arn:aws:iam::${var.member_account_id}:role/${var.member_access_role}"
    session_name = "terraform-bedrock-cost-lab"
  }
}

locals {
  callers = {
    alice-a  = { owner = "lab-alice", product = "lab-product-a" }
    bob-a    = { owner = "lab-bob", product = "lab-product-a" }
    alice-b  = { owner = "lab-alice", product = "lab-product-b" }
    untagged = {}
  }
  runtime_model_id = "openai.gpt-oss-20b-1:0"
  mantle_model_id  = "openai.gpt-oss-20b"
}

data "aws_iam_policy_document" "trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.management_account_id}:role/${var.invoker_role_name}"]
    }
  }
}

resource "aws_iam_role" "caller" {
  for_each             = local.callers
  name                 = "bedrock-cost-lab-${each.key}"
  description          = "Synthetic caller for IAM principal cost attribution"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
  tags                 = each.value
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
    resources = ["arn:aws:bedrock-mantle:${var.region}:${var.member_account_id}:project/default"]
    condition {
      test     = "StringEquals"
      variable = "bedrock-mantle:Model"
      values   = [local.mantle_model_id]
    }
  }
}

resource "aws_iam_role_policy" "inference" {
  for_each = local.callers
  name     = "bounded-bedrock-inference"
  role     = aws_iam_role.caller[each.key].id
  policy   = data.aws_iam_policy_document.inference.json
}

output "test_config" {
  value = {
    management_account_id = var.management_account_id
    member_account_id     = var.member_account_id
    member_access_role    = var.member_access_role
    region                = var.region
    runtime_model_id      = local.runtime_model_id
    mantle_model_id       = local.mantle_model_id
    mantle_project_id     = "default"
    session_tag_callers   = local.session_tag_callers
    callers = {
      for key, role in aws_iam_role.caller : key => {
        arn  = role.arn
        tags = local.callers[key]
      }
    }
  }
}
