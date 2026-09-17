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
    condition     = can(regex("^[0-9]{12}$", var.member_account_id))
    error_message = "Provide the intended member account ID."
  }
}

variable "region" {
  type    = string
  default = "eu-central-1"
  validation {
    condition     = startswith(var.region, "eu-")
    error_message = "Store billing exports in a European region."
  }
}

variable "budget_email" {
  type      = string
  sensitive = true
}

variable "monthly_budget_usd" {
  type    = number
  default = 10
  validation {
    condition     = var.monthly_budget_usd > 0
    error_message = "The budget must be positive."
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

provider "aws" {
  alias               = "billing"
  region              = "us-east-1"
  allowed_account_ids = [var.management_account_id]
}

data "aws_organizations_organization" "existing" {}

module "reports" {
  source = "../modules/private-bucket"
  name   = "bedrock-cost-reports-${var.management_account_id}-${var.region}"
}

data "aws_iam_policy_document" "reports" {
  statement {
    sid       = "RequireTLS"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [module.reports.arn, "${module.reports.arn}/*"]
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

  statement {
    sid       = "ExportBucketChecks"
    actions   = ["s3:GetBucketAcl", "s3:GetBucketPolicy"]
    resources = [module.reports.arn]
    principals {
      type        = "Service"
      identifiers = ["bcm-data-exports.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.management_account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bcm-data-exports:us-east-1:${var.management_account_id}:export/*"]
    }
  }

  statement {
    sid       = "ExportDelivery"
    actions   = ["s3:PutObject"]
    resources = ["${module.reports.arn}/cur/*"]
    principals {
      type        = "Service"
      identifiers = ["bcm-data-exports.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [var.management_account_id]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = ["arn:aws:bcm-data-exports:us-east-1:${var.management_account_id}:export/*"]
    }
  }
}

resource "aws_s3_bucket_policy" "reports" {
  bucket = module.reports.id
  policy = data.aws_iam_policy_document.reports.json
}

resource "aws_bcmdataexports_export" "lab" {
  provider = aws.billing
  export {
    name        = "bedrock-cost-lab"
    description = "Member-account billing with IAM principal attribution"
    data_query {
      query_statement = <<-SQL
        SELECT identity_line_item_id, bill_billing_period_start_date,
          line_item_usage_account_id, line_item_usage_start_date,
          line_item_usage_end_date, line_item_line_item_type,
          line_item_product_code, line_item_usage_type, line_item_operation,
          line_item_iam_principal, line_item_usage_amount,
          line_item_unblended_cost, line_item_currency_code,
          product, pricing_unit, tags
        FROM COST_AND_USAGE_REPORT
        WHERE line_item_usage_account_id = '${var.member_account_id}'
      SQL
      table_configurations = {
        COST_AND_USAGE_REPORT = {
          BILLING_VIEW_ARN                      = "arn:aws:billing::${var.management_account_id}:billingview/primary"
          TIME_GRANULARITY                      = "HOURLY"
          INCLUDE_IAM_PRINCIPAL_DATA            = "TRUE"
          INCLUDE_RESOURCES                     = "FALSE"
          INCLUDE_SPLIT_COST_ALLOCATION_DATA    = "FALSE"
          INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY = "FALSE"
        }
      }
    }
    destination_configurations {
      s3_destination {
        s3_bucket = module.reports.id
        s3_prefix = "cur"
        s3_region = var.region
        s3_output_configurations {
          overwrite   = "OVERWRITE_REPORT"
          format      = "TEXT_OR_CSV"
          compression = "GZIP"
          output_type = "CUSTOM"
        }
      }
    }
    refresh_cadence {
      frequency = "SYNCHRONOUS"
    }
  }
  depends_on = [aws_s3_bucket_policy.reports]

  lifecycle {
    precondition {
      condition = (
        data.aws_organizations_organization.existing.master_account_id == var.management_account_id &&
        contains(data.aws_organizations_organization.existing.accounts[*].id, var.member_account_id) &&
        var.member_account_id != var.management_account_id
      )
      error_message = "The billing provider must own the existing organization and the selected member account."
    }
  }
}

resource "aws_budgets_budget" "lab" {
  provider     = aws.billing
  account_id   = var.management_account_id
  name         = "bedrock-cost-lab-member-account"
  budget_type  = "COST"
  limit_amount = tostring(var.monthly_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  cost_filter {
    name   = "LinkedAccount"
    values = [var.member_account_id]
  }

  dynamic "notification" {
    for_each = [50, 100]
    content {
      comparison_operator        = "GREATER_THAN"
      threshold                  = notification.value
      threshold_type             = "PERCENTAGE"
      notification_type          = "ACTUAL"
      subscriber_email_addresses = [var.budget_email]
    }
  }
}

output "reports_bucket" {
  value = module.reports.id
}

output "export_arn" {
  value = aws_bcmdataexports_export.lab.arn
}
