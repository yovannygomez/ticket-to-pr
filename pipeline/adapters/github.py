"""The ticket's working copy and its pull request on GitHub.

The worker holds the repository token and does every push itself; the
sandbox only ever sees the files. The token goes in a per-command header,
never into the clone's config, so it isn't on disk in the working copy.
"""

import asyncio
import base64
from pathlib import Path

import httpx

from pipeline.models import CheckReport, Implementation, PullRequest, PullRequestInput

CI_POLL_SECONDS = 20
CI_START_GRACE_POLLS = 9  # about three minutes for CI to pick the commit up


class GitError(RuntimeError):
    pass


async def git(cwd: Path, *args: str, auth: str | None = None) -> str:
    prefix = ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {auth}"] if auth else []
    process = await asyncio.create_subprocess_exec(
        "git", *prefix, *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await process.communicate()
    if process.returncode:
        raise GitError(f"git {args[0]} failed: {err.decode().strip()}")
    return out.decode().strip()


class Workspace:
    """One clone per ticket under `root`, reset to what's pushed before each
    step, so a retried step starts from the same state."""

    def __init__(self, root: Path, repository: str, token: str):
        self.root = root
        self.repository = repository
        self.auth = base64.b64encode(f"x-access-token:{token}".encode()).decode()

    async def prepare(self, key: str, branch: str) -> Path:
        path = self.root / key
        if not (path / ".git").exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            await git(self.root, "clone", f"https://github.com/{self.repository}.git", key, auth=self.auth)
            await git(path, "config", "user.name", "ticket-to-pr")
            await git(path, "config", "user.email", "ticket-to-pr@users.noreply.github.com")
        await git(path, "fetch", "origin", auth=self.auth)
        remote = await git(path, "ls-remote", "--heads", "origin", branch, auth=self.auth)
        await git(path, "checkout", "-B", branch, f"origin/{branch}" if remote else "origin/main")
        await git(path, "reset", "--hard")
        await git(path, "clean", "-fd")
        return path

    async def commit_and_push(self, path: Path, branch: str, message: str, *paths: str) -> str:
        await git(path, "add", "-A", "--", *(paths or (".",)))
        if await git(path, "status", "--porcelain"):
            await git(path, "commit", "-m", message)
        await git(path, "push", "origin", f"HEAD:refs/heads/{branch}", auth=self.auth)
        return await git(path, "rev-parse", "HEAD")


class GitHubRepository:
    def __init__(self, repository: str, token: str, reviewer: str):
        self.repository = repository
        self.reviewer = reviewer
        self.owner = repository.split("/")[0]
        self.http = httpx.AsyncClient(
            base_url=f"https://api.github.com/repos/{repository}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=20,
            follow_redirects=True,
        )

    async def open_pull_request(self, request: PullRequestInput) -> PullRequest:
        """Idempotent: an open PR for the branch is returned, not duplicated."""
        branch = request.implementation.branch
        existing = (
            await self.http.get("/pulls", params={"head": f"{self.owner}:{branch}", "state": "open"})
        ).raise_for_status()
        if existing.json():
            pull = existing.json()[0]
            return PullRequest(number=pull["number"], url=pull["html_url"])
        created = await self.http.post(
            "/pulls",
            json={
                "title": f"{request.ticket.key}: {request.ticket.title}",
                "head": branch,
                "base": "main",
                "body": pull_request_body(request),
                "draft": True,
            },
        )
        pull = created.raise_for_status().json()
        return PullRequest(number=pull["number"], url=pull["html_url"])

    async def wait_for_checks(self, implementation: Implementation) -> CheckReport:
        """Polls the check runs on the pushed commit until all have finished.
        A failure comes back with the tail of each failed job's log, which is
        what the agent gets to fix it."""
        sha = implementation.commit_sha
        for poll in range(10_000):
            runs = (await self.http.get(f"/commits/{sha}/check-runs")).raise_for_status().json()["check_runs"]
            if not runs and poll >= CI_START_GRACE_POLLS:
                return CheckReport(passed=False, failures=[f"no CI run started for {sha[:7]}"])
            if runs and all(run["status"] == "completed" for run in runs):
                failed = [run for run in runs if run["conclusion"] not in ("success", "skipped", "neutral")]
                return CheckReport(passed=not failed, failures=[await self._failure(run) for run in failed])
            await asyncio.sleep(CI_POLL_SECONDS)
        raise TimeoutError(sha)

    async def _failure(self, run: dict) -> str:
        log = await self.http.get(f"/actions/jobs/{run['id']}/logs")
        lines = log.text.splitlines()[-40:] if log.status_code == 200 else [f"(log unavailable: {log.status_code})"]
        return f"CI check '{run['name']}' {run['conclusion']}:\n" + "\n".join(lines)

    async def hand_to_reviewer(self, pull_request: PullRequest) -> None:
        """Out of draft (GraphQL only), then assigned to the reviewer. A
        review request would fail when the reviewer is the token's owner,
        so the assignee is what always works."""
        pull = (await self.http.get(f"/pulls/{pull_request.number}")).raise_for_status().json()
        if pull["draft"]:
            mutation = (
                "mutation($id: ID!) { markPullRequestReadyForReview(input: {pullRequestId: $id}) { clientMutationId } }"
            )
            (
                await self.http.post(
                    "https://api.github.com/graphql", json={"query": mutation, "variables": {"id": pull["node_id"]}}
                )
            ).raise_for_status()
        (
            await self.http.post(f"/issues/{pull_request.number}/assignees", json={"assignees": [self.reviewer]})
        ).raise_for_status()
        if pull["user"]["login"] != self.reviewer:
            await self.http.post(
                f"/pulls/{pull_request.number}/requested_reviewers", json={"reviewers": [self.reviewer]}
            )


def pull_request_body(request: PullRequestInput) -> str:
    return "\n\n".join(
        [
            f"Implements **{request.ticket.key}**. Written by an agent from the approved spec; "
            "review it like any other PR.",
            request.spec.markdown,
            "## Gates (all passed)\n"
            "- ruff check\n- pytest\n- existing tests unchanged vs main\n"
            "- approved tests unchanged since approval\n- no new skip or xfail\n"
            "- main's tests pass on this branch",
            f"## Agent summary\n{request.implementation.summary}",
        ]
    )
