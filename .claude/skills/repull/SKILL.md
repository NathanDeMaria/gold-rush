---
name: repull
description: Re-pull a range of gold-rush markets on AWS Batch after a fix -- a call-it-what-you-want row, a matcher change -- has merged, and say what it changed. Waits for CI's image of the fix first, since a pull submitted before it lands runs the old code and succeeds anyway. Use when asked to backfill, re-pull, or pull a range again.
argument-hint: "<venue> <league> <start> <end> [...more ranges]"
---

# Re-pulling a range

A re-pull is the daily job with dates: `gold-rush pull <venue> <league>
<start> <end>` on the `gold-rush-pull` definition, which replaces each day's
file whole. `jobs/README.md` has the plain `submit-job`; this is the order to
do it in so the result can be trusted.

Every `aws` call below takes `--profile <name>` when one was given. An
`AWS_PROFILE` in the environment isn't enough if access keys are there too:
the CLI prefers the keys, and the calls go out as the wrong principal.

## 1. Wait for the image

The definition runs `gold-rush:latest` and a submit can't override it. So
first make sure `latest` is the build of the commit with the fix:

```bash
jobs/wait-for-image.sh [--profile <name>] [<sha>]   # default: origin/main
```

Run it in the background; it exits when there's an answer. 0 means submit.
1 means the Image run failed or went green without pushing, and 2 means
there's no Image run for that commit -- either way, stop and report the line
it printed. Don't submit on anything but 0.

## 2. Submit

One job per venue and league. Name them `gold-rush-repull-<venue>-<league>`
so they're findable, and chain a venue's leagues with `--depends-on
jobId=<previous>` -- every job leaves through the same few instances, which a
venue sees as one client, so a venue's pulls go one after another. The two
venues are different hosts and can run side by side.

```bash
aws batch submit-job --job-name gold-rush-repull-polymarket-mens \
  --job-queue job-queue --job-definition gold-rush-pull \
  --container-overrides '{"command": ["pull", "polymarket", "mens", "2025-11-03", "2026-09-28"]}' \
  [--depends-on jobId=<previous>] --query jobId --output text
```

Timing, to know when to worry: a job sits RUNNABLE five to ten minutes
waiting for an instance. Then, from the September 2026 backfill, a season
takes about 15 minutes of basketball or 5 of football on Polymarket, and
35-45 of basketball, 25 of college football and 6 of NFL on Kalshi, which
paces itself harder.

## 3. Watch

Poll `aws batch describe-jobs --jobs <ids>` every minute or two from a
background command that exits once every job is SUCCEEDED or FAILED. A
FAILED job fails the ones chained after it; report which and why
(`statusReason`, then the log stream under `/aws/batch/job`).

## 4. Compare

Each pull writes `markets/_pulls/<venue>/<league>/<started>.json`. Compare
the new one with **the previous pull of the same range** -- by its `start`
and `end`, not by being second-newest, since the daily run of an
out-of-season league writes an empty summary in between.

Report, per pull: listed, matched, unmatched, priced, errors -- old and new
-- and whether the misses the fix was for are gone.

**Check `errors`.** A request that fails leaves its game matched but
unpriced, and because the day's file is replaced whole, that day now lacks a
game the previous pull had. Re-pull each such day on its own (`<day>
<day>`); that's a fix, not a retry to hide, so say it happened.
