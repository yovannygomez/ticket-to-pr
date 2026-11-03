"""Deterministic gates, run inside the sandbox on the ticket's working copy.

Prints one JSON line: {"passed": bool, "failures": [...]}. No model is
involved: the same code always gets the same verdict.

    python gates.py <approved-tests-sha> <approved test file>...
"""

import json
import re
import subprocess
import sys

SKIP = re.compile(r"^\+.*(pytest\.mark\.(skip|xfail)|pytest\.skip\()", re.MULTILINE)


def run(*command: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, text=True)


def tail(process: subprocess.CompletedProcess, lines: int = 15) -> str:
    return "\n".join((process.stdout + process.stderr).strip().splitlines()[-lines:])


def main(tests_sha: str, approved: list[str]) -> list[str]:
    failures = []

    lint = run("ruff", "check", ".")
    if lint.returncode:
        failures.append(f"ruff check failed:\n{tail(lint)}")

    tests = run("python", "-m", "pytest", "-q")
    if tests.returncode:
        failures.append(f"pytest failed:\n{tail(tests)}")

    # Existing tests must not change: modified or deleted vs main fails.
    changed = run("git", "diff", "--name-only", "--diff-filter=MD", "origin/main...HEAD", "--", "tests/")
    if changed.stdout.strip():
        failures.append(f"existing tests modified or deleted: {changed.stdout.split()}")

    # The approved new tests must be exactly as approved.
    if approved:
        touched = run("git", "diff", "--name-only", tests_sha, "HEAD", "--", *approved)
        if touched.stdout.strip():
            failures.append(f"approved tests changed after approval: {touched.stdout.split()}")

    # No new skips or expected failures.
    diff = run("git", "diff", "origin/main...HEAD", "--", "tests/")
    if SKIP.search(diff.stdout):
        failures.append("a skip or xfail was added to the tests")

    # Main's version of the existing tests, run against this branch's code.
    # `git checkout origin/main -- tests/` restores files that exist on main
    # and leaves the branch's new files, so one run covers both.
    run("git", "checkout", "origin/main", "--", "tests/")
    main_tests = run("python", "-m", "pytest", "-q")
    run("git", "checkout", "HEAD", "--", "tests/")
    if main_tests.returncode:
        failures.append(f"main's tests fail on this branch:\n{tail(main_tests)}")

    return failures


if __name__ == "__main__":
    failures = main(sys.argv[1], sys.argv[2:])
    print(json.dumps({"passed": not failures, "failures": failures}))
