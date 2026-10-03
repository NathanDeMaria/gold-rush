provider "aws" {
  region = var.aws_region
}

# The shared account-level infrastructure: queue, compute environment, bucket,
# ECR repos, and the two roles that don't vary by app. Read rather than
# redeclared, so there is exactly one of each.
#
# aws-batch-optimization publishes its non-sensitive outputs as one JSON
# parameter (its infra/ssm.tf), in the same shape as a `terraform_remote_state`
# `outputs`, so this stack doesn't need to know where that one keeps its state.
data "aws_ssm_parameter" "shared" {
  name = var.shared_outputs_parameter
}

data "aws_caller_identity" "current" {}

data "aws_partition" "current" {}

locals {
  shared = jsondecode(data.aws_ssm_parameter.shared.insecure_value)

  image = "${local.shared.repo_urls["gold-rush"]}:${var.image_tag}"

  # The bucket endgame's seasons live in, which a pull reads to match against,
  # and where it writes under `markets/`.
  bucket = local.shared.bucket

  # The region, for every job, for the reason cassandra's jobs/main.tf gives
  # at length: a container has no ~/.aws/config, and a client that doesn't
  # fall back to instance metadata resolves an empty region and fails every
  # request. Nothing here is known to need it today; that's not the property
  # worth relying on.
  job_environment = [
    { name = "AWS_DEFAULT_REGION", value = var.aws_region },
  ]

  # One scheduled pull per venue and league. Each league starts
  # `league_spacing_minutes` after the one before it, both venues at once --
  # see the variable for why. Keyed "<venue>-<league>" for the schedule names.
  pulls = {
    for pair in setproduct(var.venues, range(length(var.leagues))) :
    "${pair[0]}-${var.leagues[pair[1]]}" => {
      venue  = pair[0]
      league = var.leagues[pair[1]]
      minute = (var.pull_hour * 60 + pair[1] * var.league_spacing_minutes) % 1440
    }
  }
}

# ------------------------------------------------------------------------------
# Job role: what gold-rush's own code is allowed to touch
# ------------------------------------------------------------------------------
# Read endgame's seasons, and read and write its own prefix. Nothing else in
# the bucket is gold-rush's to change, so writes are scoped to `markets/`.
data "aws_iam_policy_document" "job_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "job" {
  name               = "${var.resource_name_prefix}-batch-job-role"
  assume_role_policy = data.aws_iam_policy_document.job_assume.json
}

data "aws_iam_policy_document" "job" {
  # Listing the seasons to find a league's, and the pull summaries for
  # `gold-rush report`. ListBucket can only be scoped by prefix with a
  # condition, and both prefixes are what it's for.
  statement {
    sid       = "ListSeasonsAndMarkets"
    actions   = ["s3:ListBucket"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${local.bucket}"]

    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["seasons/*", "markets/*"]
    }
  }

  statement {
    sid     = "ReadSeasonsAndMarkets"
    actions = ["s3:GetObject"]
    resources = [
      "arn:${data.aws_partition.current.partition}:s3:::${local.bucket}/seasons/*",
      "arn:${data.aws_partition.current.partition}:s3:::${local.bucket}/markets/*",
    ]
  }

  statement {
    sid       = "WriteMarkets"
    actions   = ["s3:PutObject"]
    resources = ["arn:${data.aws_partition.current.partition}:s3:::${local.bucket}/markets/*"]
  }
}

resource "aws_iam_role_policy" "job" {
  name   = "${var.resource_name_prefix}-job"
  role   = aws_iam_role.job.name
  policy = data.aws_iam_policy_document.job.json
}

# ------------------------------------------------------------------------------
# The job
# ------------------------------------------------------------------------------
# One definition; every schedule and every backfill is this with a different
# command. The default command is the report, so submitting it bare is
# harmless.
module "pull" {
  source = "git::https://github.com/NathanDeMaria/aws-batch-optimization.git//infra/modules/batch_job?ref=main"

  job_name           = "${var.resource_name_prefix}-pull"
  image              = local.image
  command            = ["report"]
  execution_role_arn = local.shared.batch_execution_role_arn
  job_role_arn       = aws_iam_role.job.arn
  # It waits on two venues' APIs, paced well under what either allows; one
  # vCPU is most of what it ever uses.
  vcpu            = 1
  memory          = var.pull_memory
  timeout_seconds = var.pull_timeout_seconds

  # The compute environment is all spot. A pull is idempotent -- a day's
  # file is replaced whole -- so a reclaimed one just runs again.
  retry_attempts = 3

  environment_variables = local.job_environment
}

# ------------------------------------------------------------------------------
# Schedules
# ------------------------------------------------------------------------------
# Daily, and all year: a league out of season lists no games, which is a
# handful of requests and an empty summary.
module "daily_pull" {
  source   = "git::https://github.com/NathanDeMaria/aws-batch-optimization.git//infra/modules/job_schedule?ref=main"
  for_each = local.pulls

  schedule_name       = "${var.resource_name_prefix}-${each.key}-daily"
  schedule_expression = "cron(${each.value.minute % 60} ${floor(each.value.minute / 60)} * * ? *)"
  schedule_timezone   = var.schedule_timezone
  job_definition      = module.pull.name
  job_queue_arn       = local.shared.job_queue_arn
  scheduler_role_arn  = local.shared.batch_scheduler_role_arn
  # No dates: `pull` defaults to yesterday, US Eastern.
  command = ["pull", each.value.venue, each.value.league]
}

# Hourly, for the games that haven't been played: every venue and league for
# today and tomorrow in one job (`gold-rush upcoming`), so a page can show the
# market before a game rather than the morning after it. One job rather than a
# schedule per pair keeps it to 24 a day on the shared queue; inside it the
# venues run side by side and each one's leagues take turns, as above. At
# `upcoming_minute` past the hour, clear of the daily pulls' quarter hours, so
# the two never hit a venue at once.
module "upcoming_pull" {
  source = "git::https://github.com/NathanDeMaria/aws-batch-optimization.git//infra/modules/job_schedule?ref=main"

  schedule_name       = "${var.resource_name_prefix}-upcoming-hourly"
  schedule_expression = "cron(${var.upcoming_minute} * * * ? *)"
  schedule_timezone   = var.schedule_timezone
  job_definition      = module.pull.name
  job_queue_arn       = local.shared.job_queue_arn
  scheduler_role_arn  = local.shared.batch_scheduler_role_arn
  command             = ["upcoming"]
}

# No failure notification here. aws-batch-optimization's alerts.tf emails on
# any job entering FAILED on the shared queue, for every app -- this one
# included -- so a topic here would be a second email about the same failure.
