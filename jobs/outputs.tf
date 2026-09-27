output "image" {
  description = "The image the job definition runs"
  value       = local.image
}

output "job_definition" {
  description = "Submit this, with a command, for a backfill or a one-off pull"
  value       = module.pull.name
}

output "job_queue_name" {
  description = "The shared queue the schedules submit to"
  value       = local.shared.job_queue_name
}

output "schedules" {
  description = "The daily pulls, and when each starts"
  value = {
    for key, pull in local.pulls :
    key => format("%02d:%02d %s", floor(pull.minute / 60), pull.minute % 60, var.schedule_timezone)
  }
}

output "job_role_arn" {
  value = aws_iam_role.job.arn
}

# ------------------------------------------------------------------------------
# CI
# ------------------------------------------------------------------------------
# Set these as repository secrets, where the workflows read them:
#   gh secret set AWS_PLAN_ROLE_ARN  --body "$(terraform output -raw ci_plan_role_arn)"
#   gh secret set AWS_APPLY_ROLE_ARN --body "$(terraform output -raw ci_apply_role_arn)"
#   gh secret set AWS_IMAGE_ROLE_ARN --body "$(terraform output -raw ci_image_role_arn)"

output "ci_plan_role_arn" {
  description = "role-to-assume for plan jobs (any branch, any PR)"
  value       = aws_iam_role.ci_plan.arn
}

output "ci_apply_role_arn" {
  description = "role-to-assume for apply jobs (main only)"
  value       = aws_iam_role.ci_apply.arn
}

output "ci_image_role_arn" {
  description = "role-to-assume for the image push (main only)"
  value       = aws_iam_role.ci_image.arn
}
