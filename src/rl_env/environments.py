"""Reference environment backends."""

from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .models import Action, EnvironmentSpec, Observation, StepOutcome, TaskSpec


class LocalProcessEnvironment:
    """Disposable local workspace for development and CI.

    This backend is not a security sandbox. Production workers should implement
    the same interface using one pod/VM per episode with an immutable verifier
    sidecar and enforced network/resource policies.
    """

    def __init__(self, spec: EnvironmentSpec) -> None:
        self.spec = spec
        self._temp_dir: tempfile.TemporaryDirectory[str] | None = None
        self._workspace: Path | None = None
        self._steps = 0

    @property
    def workspace(self) -> Path:
        if self._workspace is None:
            raise RuntimeError("environment has not been reset")
        return self._workspace

    async def reset(self, task: TaskSpec) -> Observation:
        if task.environment_revision != self.spec.revision:
            raise ValueError(
                f"task requires {task.environment_revision}, got {self.spec.revision}"
            )
        await self.close()
        self._temp_dir = tempfile.TemporaryDirectory(prefix="rl-env-")
        self._workspace = Path(self._temp_dir.name) / "workspace"
        source = self._source_path()
        if source:
            shutil.copytree(source, self._workspace)
        else:
            self._workspace.mkdir()
        self._materialize_initial_files(task.initial_state.get("files", {}))
        self._steps = 0
        return Observation(
            message=task.prompt,
            state={"workspace": str(self.workspace), "step": 0},
        )

    async def step(self, action: Action) -> StepOutcome:
        self._steps += 1
        if action.kind == "submit":
            return StepOutcome(
                Observation(
                    "submission received",
                    {"step": self._steps, "workspace": str(self.workspace)},
                ),
                terminated=True,
            )
        if action.kind != "exec" or "exec" not in self.spec.capabilities:
            return StepOutcome(
                Observation(
                    f"unsupported action: {action.kind}",
                    {
                        "step": self._steps,
                        "workspace": str(self.workspace),
                        "error": "unsupported_action",
                    },
                )
            )

        argv = action.payload.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or not all(isinstance(item, str) for item in argv)
        ):
            return StepOutcome(
                Observation(
                    "exec requires a non-empty string argv list",
                    {
                        "step": self._steps,
                        "workspace": str(self.workspace),
                        "error": "invalid_action",
                    },
                )
            )

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.workspace,
                env=self._clean_environment(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.spec.step_timeout_seconds
            )
            state: dict[str, Any] = {
                "step": self._steps,
                "workspace": str(self.workspace),
                "exit_code": process.returncode,
                "stdout": stdout.decode(errors="replace")[-64_000:],
                "stderr": stderr.decode(errors="replace")[-64_000:],
            }
            return StepOutcome(Observation("command completed", state))
        except TimeoutError:
            process.kill()
            await process.wait()
            return StepOutcome(
                Observation(
                    "command timed out",
                    {
                        "step": self._steps,
                        "workspace": str(self.workspace),
                        "error": "timeout",
                    },
                ),
                truncated=True,
            )

    async def close(self) -> None:
        if self._temp_dir is not None:
            self._temp_dir.cleanup()
            self._temp_dir = None
            self._workspace = None

    def _source_path(self) -> Path | None:
        value = self.spec.image
        if value == "empty://":
            return None
        if value.startswith("local://"):
            path = Path(value.removeprefix("local://")).resolve()
            if not path.is_dir():
                raise ValueError(f"local snapshot does not exist: {path}")
            return path
        raise ValueError(
            "LocalProcessEnvironment only supports empty:// and local:// snapshots"
        )

    def _materialize_initial_files(self, files: object) -> None:
        if not isinstance(files, dict):
            raise ValueError("initial_state.files must be an object")
        root = self.workspace.resolve()
        for relative, content in files.items():
            if not isinstance(relative, str) or not isinstance(content, str):
                raise ValueError("initial files must map paths to string contents")
            target = (root / relative).resolve()
            if root not in target.parents:
                raise ValueError(f"initial file escapes workspace: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")

    @staticmethod
    def _clean_environment() -> dict[str, str]:
        allowed = ("PATH", "LANG", "LC_ALL", "TZ")
        return {key: os.environ[key] for key in allowed if key in os.environ}


class KeyValueEnvironment:
    """Deterministic example backend useful for smoke tests and tutorials."""

    def __init__(self, spec: EnvironmentSpec) -> None:
        self.spec = spec
        self.state: dict[str, Any] = {}

    async def reset(self, task: TaskSpec) -> Observation:
        if task.environment_revision != self.spec.revision:
            raise ValueError("task and environment revisions do not match")
        values = task.initial_state.get("values", {})
        if not isinstance(values, dict):
            raise ValueError("initial_state.values must be an object")
        self.state = dict(values)
        return Observation(task.prompt, {"values": dict(self.state)})

    async def step(self, action: Action) -> StepOutcome:
        if action.kind == "set":
            key, value = action.payload.get("key"), action.payload.get("value")
            if not isinstance(key, str):
                return StepOutcome(Observation("key must be a string", {"values": self.state}))
            self.state[key] = value
            return StepOutcome(Observation("value updated", {"values": dict(self.state)}))
        if action.kind == "submit":
            return StepOutcome(
                Observation("submission received", {"values": dict(self.state)}),
                terminated=True,
            )
        return StepOutcome(Observation("unsupported action", {"values": dict(self.state)}))

    async def close(self) -> None:
        return None
