"""Runs Claude Code and the gates in a throwaway container.

Locally that's `docker run --rm`; on AWS the same image runs as a Fargate
task. The container gets the working copy and the model key only.
"""

import asyncio
import json
import os
from pathlib import Path

from pipeline import tracing
from pipeline.models import GateReport

SETUP = "pip install --user -q --disable-pip-version-check -r requirements-dev.txt"


class SandboxError(RuntimeError):
    pass


class Sandbox:
    def __init__(self, image: str, anthropic_key: str):
        self.image = image
        self.anthropic_key = anthropic_key

    async def _run(self, name: str, path: Path, script: str, env: dict[str, str]) -> str:
        # A container left by a dead worker would hold the same name: remove
        # it first, so a retried step never runs next to its ghost.
        await (await asyncio.create_subprocess_exec("docker", "rm", "-f", name, stdout=-3, stderr=-3)).wait()
        env_args = [arg for key in env for arg in ("-e", key)]
        process = await asyncio.create_subprocess_exec(
            "docker", "run", "--rm", "--name", name, "-v", f"{path}:/work", *env_args, self.image, "sh", "-c", script,
            env={**os.environ, **env},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )  # fmt: skip
        out, err = await process.communicate()
        if process.returncode:
            raise SandboxError(f"{name} exited {process.returncode}: {(err or out).decode()[-2000:]}")
        return out.decode()

    async def claude(self, ticket: str, step: str, path: Path, prompt: str, settings: str, model: str) -> str:
        """Claude Code headless; returns its final message. Tokens and cost
        go to LangFuse as one generation per run."""
        script = (
            f'{SETUP} && claude -p "$PROMPT" --model {model} '
            f"--settings /etc/ticket-to-pr/{settings} --output-format json"
        )
        with tracing.generation(ticket, f"claude-code:{step}", model, prompt) as observation:
            raw = await self._run(
                f"ttp-{ticket}-{step}", path, script, {"PROMPT": prompt, "ANTHROPIC_API_KEY": self.anthropic_key}
            )
            result = json.loads(raw.strip().splitlines()[-1])
            usage = result.get("usage", {})
            observation.update(
                output=result.get("result"),
                usage_details={
                    "input": usage.get("input_tokens", 0),
                    "output": usage.get("output_tokens", 0),
                    "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
                    "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
                },
                cost_details={"total": result.get("total_cost_usd", 0.0)},
                metadata={"turns": result.get("num_turns"), "duration_ms": result.get("duration_ms")},
            )
        if result.get("is_error"):
            raise SandboxError(f"Claude Code {step} ended with an error: {result.get('result')}")
        return result.get("result") or ""

    async def gates(self, ticket: str, path: Path, tests_sha: str, test_files: list[str]) -> GateReport:
        files = " ".join(test_files)
        raw = await self._run(
            f"ttp-{ticket}-gates", path, f"{SETUP} && python /opt/ticket-to-pr/gates.py {tests_sha} {files}", {}
        )
        report = json.loads(raw.strip().splitlines()[-1])
        return GateReport(passed=report["passed"], failures=report["failures"])


class SandboxGates:
    """The gates on exactly what was pushed: the working copy is reset to the
    branch first, so nothing unpushed can make them pass."""

    def __init__(self, workspace, sandbox: Sandbox):
        self.workspace = workspace
        self.sandbox = sandbox

    async def run(self, request) -> GateReport:
        branch = request.implementation.branch
        key = branch.removeprefix("feature/")
        path = await self.workspace.prepare(key, branch)
        return await self.sandbox.gates(key, path, request.tests.commit_sha, request.tests.files)
