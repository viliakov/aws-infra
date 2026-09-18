terraform {
  required_version = ">= 1.16.3, < 2.0"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "= 6.65.0"
    }
  }
}

variable "management_account_id" {
  type = string
  validation {
    condition     = can(regex("^[0-9]{12}$", var.management_account_id))
    error_message = "Provide the intended management account ID."
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
  validation {
    condition     = startswith(var.region, "eu-")
    error_message = "Store Terraform state in a European region."
  }
}

provider "aws" {
  region              = var.region
  allowed_account_ids = [var.management_account_id]
  default_tags {
    tags = {
      ManagedBy = "Terraform"
      Purpose   = "bedrock-cost-lab"
    }
  }
}

module "state" {
  source = "../modules/private-bucket"
  name   = "aws-infra-state-${var.management_account_id}-${var.region}"
}

data "aws_iam_policy_document" "state" {
  statement {
    sid       = "RequireTLS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [module.state.arn, "${module.state.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = module.state.id
  policy = data.aws_iam_policy_document.state.json
}

output "state_bucket" {
  value = module.state.id
}
