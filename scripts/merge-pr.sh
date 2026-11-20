#!/usr/bin/env bash
# Merge a pull request only when every check on it has passed (Noble Search PRB-0005).
# GitHub doesn't enforce branch rules on private repos on this plan, so this script is the gate.
#
#   scripts/merge-pr.sh <pr-number> [--squash | --merge] [--subject "..."] [--dry-run]
#
# Waits for the checks to finish, refuses to merge if any check failed or was cancelled, if the PR is a
# draft, already closed, or can't merge cleanly, then merges and deletes the branch.
set -euo pipefail

DEFAULT_METHOD="merge"    # merge commits here: the spec manifest cites this repo's commit IDs
WAIT_FOR_CHECKS_SECONDS=300   # how long to wait for checks to appear on a new PR

die() { echo "merge-pr: $*" >&2; exit 1; }
command -v gh >/dev/null || die "the GitHub CLI (gh) is required"

pr="${1:-}"; [[ "${pr}" =~ ^[0-9]+$ ]] || die "usage: scripts/merge-pr.sh <pr-number> [--squash|--merge] [--subject \"...\"] [--dry-run]"
shift
method="$DEFAULT_METHOD"; extra=(); dry=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --squash|--merge|--rebase) method="${1#--}"; shift ;;
    --dry-run) dry=1; shift ;;
    --subject) [[ $# -ge 2 ]] || die "--subject needs a value"; extra+=(--subject "$2"); shift 2 ;;
    *) die "unknown option: $1" ;;
  esac
done

state=$(gh pr view "${pr}" --json state,isDraft -q '"\(.state) \(.isDraft)"') || die "PR #${pr} not found"
[[ "$state" == "OPEN false" ]] || die "PR #${pr} is not an open, ready PR ($state)"

# Wait for checks to be reported (a just-pushed PR may have none yet).
waited=0
until [[ "$(gh pr checks "${pr}" --json name -q 'length' 2>/dev/null || echo 0)" -gt 0 ]]; do
  (( waited >= WAIT_FOR_CHECKS_SECONDS )) && die "no checks reported on PR #${pr} after ${WAIT_FOR_CHECKS_SECONDS}s; not merging"
  sleep 10; waited=$((waited + 10))
done

echo "merge-pr: waiting for checks on PR #${pr}…"
gh pr checks "${pr}" --watch --fail-fast --interval 10 >/dev/null 2>&1 || true

# Decide from the final results, not the watch exit code: every check must pass (or be skipped).
results=$(gh pr checks "${pr}" --json name,bucket -q '.[] | "\(.bucket)\t\(.name)"')
echo "$results" | sed 's/^/  /'
if echo "$results" | grep -qvE '^(pass|skipping)'$'\t'; then
  die "not merging PR #${pr}: a check did not pass"
fi

ms=$(gh pr view "${pr}" --json mergeable -q .mergeable)
[[ "$ms" == "MERGEABLE" ]] || die "PR #${pr} can't merge cleanly ($ms); update the branch first"

if (( dry )); then echo "merge-pr: dry run: PR #${pr} would merge ($method)"; exit 0; fi
gh pr merge "${pr}" "--$method" --delete-branch ${extra[@]+"${extra[@]}"} >/dev/null
sha=$(gh pr view "${pr}" --json state,mergeCommit -q 'if .state == "MERGED" then .mergeCommit.oid[0:7] else "" end')
[[ -n "$sha" ]] || die "merge of PR #${pr} did not complete"
echo "merge-pr: PR #${pr} merged ($method) as $sha"
