#!/usr/bin/env bash
# Wait until `gold-rush:latest` in ECR is the image built from a commit.
#
#   jobs/wait-for-image.sh                    # origin/main, after a fetch
#   jobs/wait-for-image.sh d19bbd8            # a particular commit
#   jobs/wait-for-image.sh --profile batch-debug
#
# The job definition runs `:latest`, and Batch can't override the image at
# submit time, so a pull submitted in the minute between a merge and CI's
# push runs the old image -- and succeeds, which is what makes it easy to
# miss. Run this first and submit only when it exits 0.
#
# ECR is checked every 30s. Past five minutes, which is several builds' worth
# (one takes about a minute), it also asks GitHub every two minutes how the
# commit's Image run is doing -- GITHUB_TOKEN is used if set, and without it
# that stays well inside the unauthenticated 60 requests an hour -- and stops
# early if there's nothing to wait for: the run failed, it
# succeeded without pushing (the workflow goes green and skips the push when
# AWS_IMAGE_ROLE_ARN isn't set), or there is no run at all.
#
# Exit 0: latest is the commit's image, or a later one that superseded it.
# Exit 1: the build failed, finished without pushing, or the wait timed out.
# Exit 2: GitHub has no Image run for the commit.
#
# --profile is passed to every aws call. AWS_PROFILE isn't enough when access
# keys are also in the environment: the CLI prefers the keys.

set -euo pipefail

REPO=NathanDeMaria/gold-rush
ECR_REPO=gold-rush
WORKFLOW=.github/workflows/image.yml
INTERVAL=${INTERVAL:-30}
SLOW_AFTER=${SLOW_AFTER:-300}
TIMEOUT=${TIMEOUT:-1200}
GITHUB_EVERY=${GITHUB_EVERY:-120}

aws_args=()
sha=""
while [ $# -gt 0 ]; do
  case "$1" in
    --profile) aws_args=(--profile "$2"); shift 2 ;;
    -h | --help) sed -n '2,/^$/s/^# \{0,1\}//p' "$0"; exit 0 ;;
    *) sha=$1; shift ;;
  esac
done

if [ -z "$sha" ]; then
  git fetch -q origin main
  sha=$(git rev-parse origin/main)
else
  sha=$(git rev-parse "$sha^{commit}")
fi
short=${sha::7}

# The other tags on `latest`, space-separated; empty if there's no such image.
latest_tags() {
  aws "${aws_args[@]}" ecr describe-images --repository-name "$ECR_REPO" \
    --image-ids imageTag=latest --query 'imageDetails[0].imageTags' \
    --output text 2>/dev/null | tr '\t' '\n' | grep -vx latest | xargs || true
}

tag_exists() {
  aws "${aws_args[@]}" ecr describe-images --repository-name "$ECR_REPO" \
    --image-ids "imageTag=$1" >/dev/null 2>&1
}

github() {
  curl -fsS ${GITHUB_TOKEN:+-H "Authorization: Bearer $GITHUB_TOKEN"} "$1"
}

# The commit's push-triggered Image run, as one JSON object, or empty.
image_run() {
  github "https://api.github.com/repos/$REPO/actions/runs?head_sha=$sha&event=push" |
    jq -c --arg path "$WORKFLOW" \
      '[.workflow_runs[] | select(.path == $path)] | max_by(.created_at) // empty'
}

# "job / step" for whatever is running or failed in a run.
run_step() {
  github "$1" | jq -r --arg want "$2" '
    [.jobs[] as $j | $j.steps[]? | select(
      if $want == "failed" then .conclusion == "failure"
      else .status == "in_progress" end) | "\($j.name) / \(.name)"]
    | first // "?"'
}

echo "waiting for $ECR_REPO:latest to be $short"
start=$(date +%s)
asked=-$GITHUB_EVERY
while true; do
  tags=$(latest_tags)
  if [[ " $tags " == *" $short "* ]]; then
    echo "latest is $short"
    exit 0
  fi
  # A later merge can push before this one's check comes round. Its image
  # was built from a descendant of this commit, so it carries the change.
  if tag_exists "$short"; then
    echo "$short was pushed, and latest has since moved on to: $tags"
    exit 0
  fi

  elapsed=$(($(date +%s) - start))
  if [ "$elapsed" -ge "$SLOW_AFTER" ] && [ $((elapsed - asked)) -ge "$GITHUB_EVERY" ]; then
    asked=$elapsed
    run=$(image_run)
    if [ -z "$run" ]; then
      echo "no Image run for $short after ${elapsed}s -- is it on main?" >&2
      exit 2
    fi
    status=$(jq -r .status <<<"$run")
    conclusion=$(jq -r .conclusion <<<"$run")
    url=$(jq -r .html_url <<<"$run")
    jobs_url=$(jq -r .jobs_url <<<"$run")
    if [ "$status" = completed ]; then
      if [ "$conclusion" = success ]; then
        # Green with no tag is the guard step skipping the push.
        echo "Image run for $short succeeded but pushed nothing" \
          "(AWS_IMAGE_ROLE_ARN unset?): $url" >&2
      else
        echo "Image run for $short $conclusion at" \
          "$(run_step "$jobs_url" failed): $url" >&2
      fi
      exit 1
    fi
    echo "${elapsed}s: Image run $status, at $(run_step "$jobs_url" running): $url"
  else
    echo "${elapsed}s: latest is ${tags:-missing}"
  fi

  if [ "$elapsed" -ge "$TIMEOUT" ]; then
    echo "gave up after ${elapsed}s; latest is ${tags:-missing}" >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
