variable "aws_region" {
  description = "AWS region to deploy into"
  type        = string
  default     = "us-east-2"
}

variable "image_tag" {
  description = "Image tag to run. CI tags every build with the short commit SHA and main's with `latest` too; pin one here to make a run reproducible."
  type        = string
  default     = "latest"
}

variable "venues" {
  description = "The venues pulled daily. Each is `gold-rush pull <venue> <league>`'s first argument."
  type        = list(string)
  default     = ["kalshi", "polymarket"]
}

variable "leagues" {
  description = <<-EOT
    The leagues pulled daily, in endgame's names -- what `gold_rush.leagues`
    knows. Order matters: each league's pulls start `league_spacing_minutes`
    after the one before it (see `local.pulls`).
  EOT
  type        = list(string)
  default     = ["nfl", "ncaafb", "mens", "womens"]
}

variable "pull_hour" {
  description = <<-EOT
    Hour of the day (in `schedule_timezone`) the first league's pulls start.

    A pull takes yesterday's games, and matches them against endgame's stored
    seasons, which endgame's own daily `games` jobs refresh at 08:00 Central.
    Starting at 10 leaves those room to finish, so yesterday's games are in the
    seasons by the time anything looks for them.
  EOT
  type        = number
  default     = 10
}

variable "upcoming_minute" {
  description = <<-EOT
    Minute past each hour the hourly pull of today's and tomorrow's games
    starts (`gold-rush upcoming`).

    Off the quarter hours the daily pulls start on (`pull_hour`,
    `league_spacing_minutes`), which each take a minute or two, so the hourly
    one never shares a venue with them.
  EOT
  type        = number
  default     = 50
}

variable "league_spacing_minutes" {
  description = <<-EOT
    Minutes between one league's pulls and the next's.

    Every job on the shared queue leaves through the same few instances, so
    both venues see concurrent pulls as one client: Kalshi's basic tier allows
    about 20 requests a second, and a pull paces itself at 8. The two venues'
    pulls run together (different hosts), but the leagues take turns, and a
    day's pull is a minute or two, so fifteen minutes keeps them from
    overlapping.
  EOT
  type        = number
  default     = 15
}

variable "schedule_timezone" {
  description = "Timezone the schedules are evaluated in"
  type        = string
  default     = "America/Chicago"
}

variable "pull_memory" {
  description = "MiB for a pull. It holds two seasons of one league and one day's price histories; a season backfill holds a season's."
  type        = number
  default     = 2048
}

variable "pull_timeout_seconds" {
  description = "Per-attempt wall clock. A day's pull is minutes and a season backfill well under an hour; this is a runaway guard, not a target."
  type        = number
  default     = 14400
}

variable "shared_infra_state" {
  description = "Where aws-batch-optimization keeps its state, read for the queue, the bucket, the ECR repo and the shared roles"
  type = object({
    bucket = string
    key    = string
    region = string
  })
  default = {
    bucket = "nathan-terraform"
    key    = "batch-state"
    region = "us-east-2"
  }
}

variable "github_repository" {
  description = "owner/repo allowed to assume the CI roles"
  type        = string
  default     = "NathanDeMaria/gold-rush"
}

variable "github_owner_id" {
  description = <<-EOT
    Numeric ID of the GitHub account owning the repository.

    GitHub issues OIDC subjects in an immutable, ID-qualified form --
    `repo:OWNER@OWNER_ID/REPO@REPO_ID:...` -- rather than by name, so the trust
    policy has to match on IDs. Matching on names alone silently never matches
    and every assume fails with a generic "Not authorized".

      gh api users/NathanDeMaria --jq .id
  EOT
  type        = number
  default     = 5595197
}

variable "github_repository_id" {
  description = <<-EOT
    Numeric ID of the repository. See github_owner_id.

      gh api repos/NathanDeMaria/gold-rush --jq .id
  EOT
  type        = number
  default     = 1389812112
}

variable "create_oidc_provider" {
  description = <<-EOT
    Create the GitHub Actions OIDC provider. Defaults false, like cassandra,
    endgame and aws-batch-optimization: IAM permits exactly one provider per
    URL per account, and invisible-string creates the one in this account.
  EOT
  type        = bool
  default     = false
}

variable "state_bucket" {
  description = "Bucket holding terraform state. Plan needs write access for the lock file."
  type        = string
  default     = "nathan-terraform"
}

variable "state_key_prefix" {
  description = <<-EOT
    Key prefix within the state bucket that CI may lock and write. Matches the
    `key` in versions.tf; the trailing `*` in the policy covers the `.tflock`
    object `use_lockfile` writes beside it.
  EOT
  type        = string
  default     = "gold-rush/jobs/terraform.tfstate"
}

variable "resource_name_prefix" {
  description = "Prefix for the IAM this stack creates. Scopes the apply role's IAM permissions."
  type        = string
  default     = "gold-rush"
}

variable "ecr_repository_name" {
  description = <<-EOT
    The ECR repository the image workflow pushes to.

    Owned by the shared stack's `repos` module, not by this one -- named here
    only so the image role's policy can be scoped to it rather than to every
    repository in the account.
  EOT
  type        = string
  default     = "gold-rush"
}

variable "shared_role_names" {
  description = <<-EOT
    Roles from the shared Batch stack that this one passes but does not manage.

    A Batch job definition is created with an execution role and an
    EventBridge schedule with a scheduler role, and creating either is an
    iam:PassRole on a role named by `aws-batch-optimization`, not by anything
    here. Listed by name rather than granting PassRole on `*`.
  EOT
  type        = list(string)
  default     = ["batch-execution-role", "batch-scheduler-role"]
}
