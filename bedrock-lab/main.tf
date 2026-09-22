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

# Kept as an empty root so existing states can remove the retired STS test roles.
