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

And hourly, at :50, one more: `gold-rush upcoming`, every venue and league
for today and tomorrow, so the games that haven't been played have a price
before they are -- the games page's market column reads them. A day's file
written during the day carries prices up to that hour, and the daily pull
the morning after replaces it with the whole game. It's one job rather than
a schedule per pair, to keep it at 24 a day on the shared queue; inside it
the two venues run side by side and each one's leagues take turns. Its
summaries go to `markets/_upcoming/` rather than `_pulls/`, so they don't
bury the daily ones -- `gold-rush report --upcoming` reads them.

## Running it by hand

A backfill, or a day that needs pulling again after a fix in
call-it-what-you-want, is the same definition with a command. The
definition runs `:latest`, so after merging a fix, wait for CI's image of
it first -- a pull submitted before it lands runs the old code and still
succeeds:

`wait-for-image.sh` in aws-batch-optimization's `batch-tools` plugin does
that wait -- see its skill. Then:

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
   `AWS_PLAN_ROLE_ARN`, `AWS_APPLY_ROLE_ARN`, `AWS_IMAGE_ROLE_ARN`.
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
