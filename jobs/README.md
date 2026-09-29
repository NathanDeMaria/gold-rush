# Batch jobs

Runs `gold-rush pull` on AWS Batch, on the shared queue from
[aws-batch-optimization][infra], the way cassandra and endgame run theirs.

[infra]: https://github.com/NathanDeMaria/aws-batch-optimization

## What's here vs. what's shared

| Shared (`aws-batch-optimization`) | Here (`gold-rush/jobs`) |
| --- | --- |
| Job queue, compute environment, network | The `gold-rush-pull` job definition |
| The bucket (endgame's seasons, and `markets/`) | The eight daily schedules |
| ECR repo `gold-rush` | `gold-rush-batch-job-role` (seasons read, `markets/` write) |
| `batch-execution-role`, `batch-scheduler-role` | The three CI roles (`oidc.tf`) |
| The `batch_job` and `job_schedule` modules | |

Failure emails are shared too: endgame's EventBridge rule emails on any job
entering FAILED on the queue, this one's included.

## The schedule

One job definition, and a daily schedule per venue and league, each running
`gold-rush pull <venue> <league>` for yesterday's games (US Eastern):

| | kalshi | polymarket |
| --- | --- | --- |
| nfl | 10:00 CT | 10:00 CT |
| ncaafb | 10:15 | 10:15 |
| mens | 10:30 | 10:30 |
| womens | 10:45 | 10:45 |

Ten o'clock so endgame's daily `games` jobs (08:00) have refreshed the
seasons a pull matches against. The leagues take turns because every job
leaves through the same few instances, which both venues see as one client;
the two venues are different hosts, so they run together.

All year, and all eight: a league out of season lists nothing, which is a
few requests and an empty summary.

## Running it by hand

A backfill, or a day that needs pulling again after a fix in
call-it-what-you-want, is the same definition with a command.

From the Actions tab, **Backfill** on main takes a venue (or both), a league
(or all), and a range of days, and submits the jobs through the
`gold-rush-ci-backfill` role. "All" chains the leagues one after another per
venue, in the schedules' order, with the venues side by side; a league that
fails fails the rest of its chain, so re-run from there. The run links each
job and returns without waiting.

Or from anywhere with credentials:

```bash
aws batch submit-job --job-name gold-rush-backfill \
  --job-queue "$(terraform output -raw job_queue_name)" \
  --job-definition "$(terraform output -raw job_definition)" \
  --container-overrides '{"command": ["pull", "kalshi", "mens", "2025-11-03", "2026-04-07"]}'
```

A season of college basketball is ~5,000 games and about half an hour per
venue. A re-pull replaces each day's file whole, so running a range twice
is safe.

`gold-rush report` from anywhere with the bucket in `~/.aws-batch/config.json`
prints what the latest pulls did; a container's stdout (CloudWatch,
`/aws/batch/job`) is the same summary.

## Setting it up

Once, in this order:

1. **The ECR repo.** `gold-rush` in aws-batch-optimization's `repos` module,
   applied by that repo's CI on merge.
2. **This stack, by hand.** `make apply` here with credentials that can
   create IAM roles -- the CI roles this stack creates can't exist before it
   does. It creates the job definition, the schedules and the three CI roles.
3. **The CI secrets**, from this stack's outputs (see `outputs.tf`):
   `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`, `AWS_IMAGE_ROLE_ARN`, and
   `AWS_BACKFILL_ROLE_ARN` for the backfill workflow.
4. **An image.** Any push to main after that builds and pushes
   `gold-rush:latest`, which the job definition runs. Until one exists, a
   scheduled job fails to pull its image and the failure email says so.

After that, CI lints and plans on branches, applies on main, and pushes an
image on every merge to main -- the same three workflows as cassandra's.

```bash
make plan     # needs credentials and terraform.tfvars
make apply
make lint     # what CI runs; no credentials
```
