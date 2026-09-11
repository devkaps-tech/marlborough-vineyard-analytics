"""
Tiny shared test harness.

The suites here run standalone (`python3 tests/<file>.py`) so verification never
depends on pytest being installed, but they also collect correctly under pytest
if it is. This module holds the small amount of plumbing that makes both work,
so each suite doesn't carry its own copy.
"""
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Make `import config` work from the suites without each one repeating this.
if str(BASE_DIR / "src") not in sys.path:
    sys.path.insert(0, str(BASE_DIR / "src"))

try:
    from pytest import skip as _pytest_skip
except ImportError:  # pragma: no cover
    _pytest_skip = None


class Skipped(Exception):
    """Raised when a check's inputs don't exist yet."""


def skip(reason: str):
    """Skip a check. Uses pytest's mechanism when available, ours otherwise."""
    if _pytest_skip is not None:
        _pytest_skip(reason)
    raise Skipped(reason)


def need(*paths: Path):
    """Skip (not fail) when an artifact hasn't been generated yet."""
    for p in paths:
        if not p.exists():
            try:
                shown = p.relative_to(BASE_DIR)
            except ValueError:
                shown = p
            skip(f"{shown} not generated yet")


def run_tests(namespace: dict, title: str) -> int:
    """
    Run every `test_*` callable in `namespace`. Returns a process exit code.

    Failures are collected and printed together at the end rather than
    interleaved, so a long run stays readable.
    """
    tests = [(n, f) for n, f in sorted(namespace.items())
             if n.startswith("test_") and callable(f)]
    passed = failed = skipped = 0
    failures = []

    print(f"{title}: running {len(tests)} checks\n")
    for name, fn in tests:
        try:
            fn()
        except Skipped as e:
            print(f"  SKIP  {name}\n          ({e})")
            skipped += 1
        except AssertionError as e:
            print(f"  FAIL  {name}")
            failures.append((name, str(e)))
            failed += 1
        except Exception as e:  # noqa: BLE001 - report, don't mask
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
            failures.append((name, f"{type(e).__name__}: {e}"))
            failed += 1
        else:
            print(f"  ok    {name}")
            passed += 1

    if failures:
        print("\n" + "=" * 70)
        for name, msg in failures:
            print(f"\n{name}:\n  {msg}")

    print("\n" + "=" * 70)
    print(f"{passed} passed, {failed} failed, {skipped} skipped")
    return 1 if failed else 0
