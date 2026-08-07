"""Episode execution and bounded-concurrency batch scheduling."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable, Sequence
from dataclasses import replace

from .interfaces import EnvironmentFactory, Policy
from .models import (
    EnvironmentSpec,
    EpisodeResult,
    EpisodeStatus,
    Observation,
    TaskSpec,
    Transition,
)
from .rewards import RewardComposer
from .store import FileTrajectoryStore


class EpisodeRunner:
    def __init__(
        self,
        environment_factory: EnvironmentFactory,
        rewards: RewardComposer,
        store: FileTrajectoryStore,
    ) -> None:
        self.environment_factory = environment_factory
        self.rewards = rewards
        self.store = store

    async def run(
        self,
        task: TaskSpec,
        environment_spec: EnvironmentSpec,
        policy: Policy,
        *,
        attempt: int = 1,
    ) -> EpisodeResult:
        episode_id = f"{task.id}-{attempt}-{uuid.uuid4().hex[:12]}"
        environment = self.environment_factory(environment_spec)
        recorder = self.store.open(episode_id)
        transitions: list[Transition] = []
        signals = ()
        observation = Observation("episode not started")
        score = 0.0

        try:
            recorder.append(
                "episode.started",
                {
                    "episode_id": episode_id,
                    "attempt": attempt,
                    "task": task,
                    "environment": environment_spec,
                },
            )
            observation = await environment.reset(task)
            recorder.append("environment.reset", observation)

            for index in range(environment_spec.max_steps):
                action = await asyncio.wait_for(
                    policy.act(task, observation, tuple(transitions)),
                    timeout=environment_spec.step_timeout_seconds,
                )
                recorder.append("agent.action", {"index": index, "action": action})
                outcome = await environment.step(action)
                transition = Transition(index=index, action=action, outcome=outcome)
                dense = await self.rewards.score_step(
                    task, transition, (*transitions, transition)
                )
                transition = replace(transition, reward=dense.score)
                transitions.append(transition)
                observation = outcome.observation
                recorder.append(
                    "environment.transition",
                    {
                        "transition": transition,
                        "reward_signals": dense.signals,
                    },
                )
                if outcome.terminated or outcome.truncated:
                    break

            final = await self.rewards.score_final(task, observation, tuple(transitions))
            score, signals = final.score, final.signals
            low, high = task.review_band
            if final.force_review or low <= score < high:
                status = EpisodeStatus.NEEDS_REVIEW
            elif score >= high:
                status = EpisodeStatus.SUCCEEDED
            else:
                status = EpisodeStatus.FAILED
            recorder.append(
                "episode.completed",
                {
                    "status": status,
                    "score": score,
                    "signals": signals,
                    "breakdown": final.breakdown,
                },
            )
            return EpisodeResult(
                episode_id=episode_id,
                task_id=task.id,
                environment_revision=environment_spec.revision,
                status=status,
                score=score,
                transitions=tuple(transitions),
                signals=signals,
                trajectory_uri=recorder.uri,
            )
        except Exception as error:
            recorder.append(
                "episode.infrastructure_error",
                {"error_type": type(error).__name__, "message": str(error)},
            )
            return EpisodeResult(
                episode_id=episode_id,
                task_id=task.id,
                environment_revision=environment_spec.revision,
                status=EpisodeStatus.INFRA_ERROR,
                score=0,
                transitions=tuple(transitions),
                signals=signals,
                trajectory_uri=recorder.uri,
                error=f"{type(error).__name__}: {error}",
            )
        finally:
            await environment.close()
            recorder.close()


class BatchScheduler:
    """In-process scheduler mirroring a distributed queue's delivery semantics."""

    def __init__(
        self,
        runner: EpisodeRunner,
        *,
        max_concurrency: int = 8,
        max_attempts: int = 2,
    ) -> None:
        if max_concurrency < 1 or max_attempts < 1:
            raise ValueError("scheduler limits must be positive")
        self.runner = runner
        self.max_concurrency = max_concurrency
        self.max_attempts = max_attempts

    async def run(
        self,
        jobs: Sequence[tuple[TaskSpec, EnvironmentSpec]],
        policy_factory: Callable[[TaskSpec], Policy],
    ) -> list[EpisodeResult]:
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def execute(
            task: TaskSpec, environment: EnvironmentSpec
        ) -> EpisodeResult:
            async with semaphore:
                result: EpisodeResult | None = None
                for attempt in range(1, self.max_attempts + 1):
                    result = await self.runner.run(
                        task,
                        environment,
                        policy_factory(task),
                        attempt=attempt,
                    )
                    if result.status is not EpisodeStatus.INFRA_ERROR:
                        break
                assert result is not None
                return result

        return await asyncio.gather(
            *(execute(task, environment) for task, environment in jobs)
        )
