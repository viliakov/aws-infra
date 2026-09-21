data "aws_iam_policy_document" "session_trust" {
  source_policy_documents = [data.aws_iam_policy_document.trust.json]

  statement {
    actions = ["sts:TagSession"]
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${var.management_account_id}:role/${var.invoker_role_name}"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["owner", "product"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/owner"
      values   = ["lab-session-alice", "lab-session-bob"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/product"
      values   = ["lab-session-product-a", "lab-session-product-b"]
    }
  }
}

resource "aws_iam_role" "session_caller" {
  name                 = "bedrock-cost-lab-session"
  description          = "Shared untagged role for session-only billing attribution"
  assume_role_policy   = data.aws_iam_policy_document.session_trust.json
  max_session_duration = 3600
  tags                 = {}
}

resource "aws_iam_role_policy" "session_inference" {
  name   = "bounded-bedrock-inference"
  role   = aws_iam_role.session_caller.id
  policy = data.aws_iam_policy_document.inference.json
}

locals {
  session_tag_callers = merge(
    {
      for key, tags in {
        session-alice    = { owner = "lab-session-alice", product = "lab-session-product-a" }
        session-bob      = { owner = "lab-session-bob", product = "lab-session-product-b" }
        session-untagged = {}
        } : key => {
        arn          = aws_iam_role.session_caller.arn
        tags         = {}
        session_tags = tags
      }
    },
    {
      static-control = {
        arn          = aws_iam_role.caller["alice-a"].arn
        tags         = local.callers["alice-a"]
        session_tags = {}
      }
    }
  )
}
