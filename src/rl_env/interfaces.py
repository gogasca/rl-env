"""Extension points for environment backends, policies, and reward systems."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from .models import (
    Action,
    EnvironmentSpec,
    Observation,
    StepOutcome,
    TaskSpec,
    Transition,
    VerificationResult,
)


class Environment(Protocol):
    async def reset(self, task: TaskSpec) -> Observation: ...

    async def step(self, action: Action) -> StepOutcome: ...

    async def close(self) -> None: ...


class EnvironmentFactory(Protocol):
    def __call__(self, spec: EnvironmentSpec) -> Environment: ...


class Policy(Protocol):
    async def act(
        self,
        task: TaskSpec,
        observation: Observation,
        trajectory: Sequence[Transition],
    ) -> Action: ...


class StepVerifier(Protocol):
    name: str

    async def evaluate_step(
        self,
        task: TaskSpec,
        transition: Transition,
        trajectory: Sequence[Transition],
    ) -> VerificationResult: ...


class FinalVerifier(Protocol):
    name: str

    async def evaluate(
        self,
        task: TaskSpec,
        final_observation: Observation,
        trajectory: Sequence[Transition],
    ) -> VerificationResult: ...


class TrajectoryRecorder(Protocol):
    @property
    def uri(self) -> str: ...

    def append(self, event_type: str, payload: Any) -> str: ...

    def close(self) -> None: ...


class TrajectoryStore(Protocol):
    def open(self, episode_id: str) -> TrajectoryRecorder: ...
