"""
Pre-publication repo hygiene checks.

This repository is PUBLIC. Anything pushed is indexed and cached by third
parties within minutes, and rewriting history afterwards does not un-publish it.
So the checks here run against the FULL COMMIT HISTORY, not just the working
tree -- a secret removed in a later commit is still a leaked secret.

These are deliberately deterministic. The judgement-shaped half of the same job
(does this change set read well, is anything sensitive-by-meaning rather than
by pattern) belongs to the `repo-auditor` agent in .claude/agents/.

Usage:
    python3 tests/test_repo_hygiene.py       # standalone
    pytest tests/test_repo_hygiene.py -v     # if pytest is installed

Wire it to run before every push:
    ln -s ../../tests/pre-push-hook.sh .git/hooks/pre-push
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import BASE_DIR, need, run_tests, skip  # noqa: E402


def git(*args: str) -> str:
    """Run a read-only git command and return stdout."""
    return subprocess.run(
        ["git", *args], cwd=BASE_DIR, capture_output=True, text=True, check=False
    ).stdout


# Files that must never be committed, by path.
FORBIDDEN_PATHS = {".env"}

# Anything under data/ except the README is either a large download or a
# reproducible pipeline output; neither belongs in git.
ALLOWED_DATA_PATHS = {"data/README.md"}

# Credential shapes. Kept deliberately narrow -- a pattern that fires on every
# occurrence of the word "password" trains people to ignore it.
SECRET_PATTERNS = [
    (r"postgres(?:ql)?://[^\s\"']*:[^\s\"'@]+@", "database URL with inline password"),
    (r"sk-[A-Za-z0-9]{20,}", "OpenAI-style API key"),
    (r"gh[pousr]_[A-Za-z0-9]{30,}", "GitHub token"),
    (r"AKIA[0-9A-Z]{16}", "AWS access key id"),
    (r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", "private key"),
    (r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.", "JWT"),
]

# Placeholders in .env.example that prove a match is a template, not a leak.
PLACEHOLDER_MARKERS = ["YOUR-PASSWORD", "project-ref", "your-password", "xxxx", "<", "["]

# A source repo with no data in it should stay small. Tripping this almost
# always means a data file or binary slipped past .gitignore.
MAX_TRACKED_FILE_KB = 512

# Deliberate exceptions, each with the reason it earns its size. Keep this list
# short and argued -- it is the pressure valve that stops the cap above being
# quietly raised, which would defeat the check entirely.
LARGE_FILE_EXCEPTIONS = {
    "dashboard.html": (
        "generated deliverable carrying ~2,900 inlined map polygons. Committed "
        "because data/ is not, so a visitor to the public repo cannot rebuild it "
        "by running src/10_build_dashboard.py"
    ),
}
MAX_EXCEPTION_FILE_KB = 2048


def _is_placeholder(line: str) -> bool:
    return any(m in line for m in PLACEHOLDER_MARKERS)


# =========================================================================
# History -- the checks that actually matter for a public repo
# =========================================================================

def test_forbidden_files_never_entered_history():
    """A .env removed in a later commit is still published in the earlier one."""
    ever = set(git("log", "--all", "--pretty=format:", "--name-only").split("\n"))
    leaked = sorted(FORBIDDEN_PATHS & ever)
    assert not leaked, (
        f"{leaked} appear(s) in commit history. Removing it now is NOT enough -- "
        f"treat the credential as compromised and rotate it, then rewrite history "
        f"with git-filter-repo before this is pushed anywhere public."
    )


def test_no_secrets_anywhere_in_history():
    revs = [r for r in git("rev-list", "--all").split("\n") if r]
    if not revs:
        skip("no commits yet")

    findings = []
    for pattern, label in SECRET_PATTERNS:
        out = git("grep", "-n", "-I", "-E", pattern, *revs)
        for line in out.split("\n"):
            if line and not _is_placeholder(line):
                findings.append(f"{label}: {line[:140]}")

    assert not findings, (
        "Credential-shaped strings found in history:\n  "
        + "\n  ".join(sorted(set(findings))[:10])
    )


def test_no_data_files_tracked():
    tracked = {f for f in git("ls-files").split("\n") if f}
    data_files = {f for f in tracked if f.startswith("data/")} - ALLOWED_DATA_PATHS
    assert not data_files, (
        f"data files are tracked: {sorted(data_files)[:5]}. Everything under "
        f"data/ is reproducible from the pipeline and must stay untracked."
    )


def test_no_oversized_tracked_files():
    oversized = []
    for f in git("ls-files").split("\n"):
        if not f:
            continue
        p = BASE_DIR / f
        if not p.exists():
            continue
        kb = p.stat().st_size / 1024
        cap = MAX_EXCEPTION_FILE_KB if f in LARGE_FILE_EXCEPTIONS else MAX_TRACKED_FILE_KB
        if kb > cap:
            oversized.append(f"{f} ({kb:.0f} KB, cap {cap} KB)")
    assert not oversized, (
        f"tracked files over their size cap: {oversized}. This repo holds source "
        f"only -- add a documented entry to LARGE_FILE_EXCEPTIONS if a generated "
        f"deliverable genuinely has to ship."
    )


# =========================================================================
# Ignore rules -- the thing standing between the above and a leak
# =========================================================================

def test_gitignore_covers_the_sensitive_paths():
    """Verify by asking git, not by reading .gitignore -- rule order is subtle."""
    must_ignore = [
        ".env",
        "data/raw/vineyard_gdb.zip",
        "data/processed/parcels_clean.parquet",
        "data/tableau/parcels.geojson",
        "extract.hyper",
    ]
    not_ignored = []
    for path in must_ignore:
        rc = subprocess.run(
            ["git", "check-ignore", "-q", path],
            cwd=BASE_DIR, capture_output=True, check=False,
        ).returncode
        if rc != 0:
            not_ignored.append(path)
    assert not not_ignored, f"NOT ignored by .gitignore: {not_ignored}"


def test_data_readme_survives_the_data_exclusion():
    """
    `data/*` + `!data/README.md` is load-bearing and easy to break: switching it
    back to `data/` silently re-excludes the README, because git cannot
    un-ignore a file inside an excluded directory.
    """
    tracked = {f for f in git("ls-files").split("\n") if f}
    assert "data/README.md" in tracked, (
        "data/README.md is not tracked -- the .gitignore negation broke. "
        "The rule must be `data/*` (not `data/`) followed by `!data/README.md`."
    )


def test_env_example_contains_no_real_credential():
    """The template is published; it must stay a template."""
    example = BASE_DIR / ".env.example"
    need(example)
    for i, line in enumerate(example.read_text().splitlines(), 1):
        if line.strip().startswith("#") or "=" not in line:
            continue
        for pattern, label in SECRET_PATTERNS:
            if re.search(pattern, line) and not _is_placeholder(line):
                raise AssertionError(
                    f".env.example:{i} looks like a real {label}, not a placeholder"
                )


# =========================================================================
# Working-tree state
# =========================================================================

def test_env_is_not_staged_right_now():
    staged = {ln[3:] for ln in git("status", "--porcelain").split("\n") if ln}
    assert not (FORBIDDEN_PATHS & staged), (
        f"{sorted(FORBIDDEN_PATHS & staged)} is staged for commit -- unstage it"
    )


def test_local_branch_is_not_behind_its_remote():
    """
    Catches the case where a push would be rejected, or worse, tempt a
    --force that discards someone else's commits.
    """
    upstream = git("rev-parse", "--abbrev-ref", "@{upstream}").strip()
    if not upstream:
        skip("branch has no upstream yet")
    counts = git("rev-list", "--left-right", "--count", f"{upstream}...HEAD").split()
    if len(counts) != 2:
        skip("could not determine divergence (remote may be unfetched)")
    behind, ahead = int(counts[0]), int(counts[1])
    assert behind == 0, (
        f"local branch is {behind} commit(s) behind {upstream} (and {ahead} ahead). "
        f"Pull and merge before pushing -- do not force."
    )


if __name__ == "__main__":
    sys.exit(run_tests(globals(), "Repo hygiene"))
