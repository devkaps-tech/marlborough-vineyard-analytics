#!/bin/bash
# Pre-push guard for a PUBLIC repository.
#
# Runs the repo hygiene checks before anything leaves the machine. A secret
# pushed to a public repo is indexed within minutes, and rewriting history
# afterwards does not un-publish it -- so this blocks rather than warns.
#
# Install:
#   ln -s ../../tests/pre-push-hook.sh .git/hooks/pre-push
#
# Bypass once, deliberately:
#   git push --no-verify

set -uo pipefail

repo_root="$(git rev-parse --show-toplevel)"

echo "pre-push: checking repo hygiene..."
if ! python3 "$repo_root/tests/test_repo_hygiene.py"; then
    echo
    echo "PUSH BLOCKED: repo hygiene checks failed (see above)."
    echo "This repo is public -- fix the finding rather than bypassing."
    echo "To override deliberately: git push --no-verify"
    exit 1
fi

# Data invariants are advisory here: they can legitimately fail mid-refactor,
# and they depend on generated artifacts that a fresh clone won't have.
if [ -f "$repo_root/data/processed/transitions.csv" ]; then
    echo "pre-push: checking data invariants (advisory)..."
    python3 "$repo_root/tests/test_invariants.py" >/dev/null 2>&1 \
        || echo "  warning: data invariants failing -- push allowed, but investigate."
fi

echo "pre-push: ok"
