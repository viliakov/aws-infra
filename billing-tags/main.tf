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

variable "principal_tag_api_keys" {
  description = "Verified IAM-principal API keys. Apply this root only after both keys appear in ListCostAllocationTags."
  type        = set(string)
  default     = ["iamPrincipal/owner", "iamPrincipal/product"]
  validation {
    condition = alltrue([
      for key in var.principal_tag_api_keys :
      contains(["iamPrincipal/owner", "iamPrincipal/product"], key)
    ])
    error_message = "Only the two verified IAM-principal keys belong in this root; resource tags are different."
  }
}

provider "aws" {
  region              = "us-east-1"
  allowed_account_ids = [var.management_account_id]
}

resource "aws_ce_cost_allocation_tag" "principal" {
  for_each = var.principal_tag_api_keys
  tag_key  = each.value
  status   = "Active"

  lifecycle {
    prevent_destroy = true
  }
}
