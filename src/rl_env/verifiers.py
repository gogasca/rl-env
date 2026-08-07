"""Reusable trusted-control-plane verifiers."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence

from .models import (
    Observation,
    RewardSignal,
    TaskSpec,
    Transition,
    VerificationResult,
)


class StateTargetVerifier:
    """Grades an observation against task.initial_state.target."""

    def __init__(self, criterion_id: str = "task_success") -> None:
        self.name = "state_target"
        self.criterion_id = criterion_id

    async def evaluate(
        self,
        task: TaskSpec,
        final_observation: Observation,
        trajectory: Sequence[Transition],
    ) -> VerificationResult:
        target = task.initial_state.get("target", {})
        values = final_observation.state.get("values", {})
        if not isinstance(target, dict) or not isinstance(values, dict):
            score, matched, total = 0.0, 0, 1
        else:
            total = max(len(target), 1)
            matched = sum(values.get(key) == value for key, value in target.items())
            score = matched / total
        return VerificationResult(
            signals=(
                RewardSignal(
                    criterion_id=self.criterion_id,
                    score=score,
                    evidence=f"{matched}/{total} target values match",
                    source=self.name,
                ),
            )
        )


class AllowedActionsVerifier:
    """Rewards trajectories that stay within a task's declared action policy."""

    def __init__(
        self,
        allowed_actions: Sequence[str],
        criterion_id: str = "policy_compliance",
    ) -> None:
        self.name = "allowed_actions"
        self.allowed_actions = frozenset(allowed_actions)
        self.criterion_id = criterion_id

    async def evaluate(
        self,
        task: TaskSpec,
        final_observation: Observation,
        trajectory: Sequence[Transition],
    ) -> VerificationResult:
        violations = [
            transition.action.kind
            for transition in trajectory
            if transition.action.kind not in self.allowed_actions
        ]
        return VerificationResult(
            signals=(
                RewardSignal(
                    criterion_id=self.criterion_id,
                    score=1.0 if not violations else 0.0,
                    evidence=(
                        "all actions allowed"
                        if not violations
                        else f"disallowed actions: {sorted(set(violations))}"
                    ),
                    source=self.name,
                ),
            )
        )


class StepProgressVerifier:
    """Provides dense progress without prescribing a single valid trajectory."""

    name = "step_progress"

    def __init__(self, criterion_id: str = "task_success") -> None:
        self.criterion_id = criterion_id

    async def evaluate_step(
        self,
        task: TaskSpec,
        transition: Transition,
        trajectory: Sequence[Transition],
    ) -> VerificationResult:
        target = task.initial_state.get("target", {})
        values = transition.outcome.observation.state.get("values", {})
        if not isinstance(target, dict) or not isinstance(values, dict):
            score = 0.0
        else:
            score = sum(values.get(key) == value for key, value in target.items()) / max(
                len(target), 1
            )
        return VerificationResult(
            signals=(
                RewardSignal(
                    criterion_id=self.criterion_id,
                    score=score,
                    evidence=f"target completion after step {transition.index}",
                    source=self.name,
                ),
            )
        )


class CommandVerifier:
    """Runs a trusted external grader and consumes its JSON output.

    The verifier command is configured by operators, never by the task or
    agent. It receives the episode workspace as its final argument and must
    output ``{"score": 0..1, "evidence": "..."}``.
    """

    def __init__(
        self,
        command: Sequence[str],
        criterion_id: str,
        *,
        timeout_seconds: float = 60,
        name: str = "command_verifier",
    ) -> None:
        if not command:
            raise ValueError("verifier command is required")
        self.command = tuple(command)
        self.criterion_id = criterion_id
        self.timeout_seconds = timeout_seconds
        self.name = name

    async def evaluate(
        self,
        task: TaskSpec,
        final_observation: Observation,
        trajectory: Sequence[Transition],
    ) -> VerificationResult:
        workspace = final_observation.state.get("workspace")
        if not isinstance(workspace, str):
            raise ValueError("environment did not expose an episode workspace")
        process = await asyncio.create_subprocess_exec(
            *self.command,
            workspace,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=self.timeout_seconds
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if process.returncode:
            raise RuntimeError(
                f"verifier failed ({process.returncode}): "
                f"{stderr.decode(errors='replace')[-4000:]}"
            )
        payload = json.loads(stdout)
        return VerificationResult(
            signals=(
                RewardSignal(
                    criterion_id=self.criterion_id,
                    score=float(payload["score"]),
                    evidence=str(payload["evidence"]),
                    source=self.name,
                ),
            ),
            force_review=bool(payload.get("force_review", False)),
            metadata={"verifier_output": payload},
        )
