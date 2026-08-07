"""Reward composition with explicit anti-gaming invariants."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from .interfaces import FinalVerifier, StepVerifier
from .models import (
    Observation,
    RewardSignal,
    TaskSpec,
    Transition,
    VerificationResult,
)


@dataclass(frozen=True, slots=True)
class ScoredReward:
    score: float
    signals: tuple[RewardSignal, ...]
    force_review: bool
    breakdown: dict[str, float]


class RewardComposer:
    """Runs independent graders and combines rubric-scoped signals.

    A source can emit at most one signal per criterion. Unknown criteria and
    duplicate signals fail closed instead of silently changing the objective.
    """

    def __init__(
        self,
        final_verifiers: Sequence[FinalVerifier],
        step_verifiers: Sequence[StepVerifier] = (),
    ) -> None:
        if not final_verifiers:
            raise ValueError("at least one final verifier is required")
        self.final_verifiers = tuple(final_verifiers)
        self.step_verifiers = tuple(step_verifiers)

    async def score_step(
        self,
        task: TaskSpec,
        transition: Transition,
        trajectory: Sequence[Transition],
    ) -> ScoredReward:
        results = await asyncio.gather(
            *(
                verifier.evaluate_step(task, transition, trajectory)
                for verifier in self.step_verifiers
            )
        )
        return (
            self.compose(task, results, enforce_required=False)
            if results
            else ScoredReward(0, (), False, {})
        )

    async def score_final(
        self,
        task: TaskSpec,
        observation: Observation,
        trajectory: Sequence[Transition],
    ) -> ScoredReward:
        results = await asyncio.gather(
            *(
                verifier.evaluate(task, observation, trajectory)
                for verifier in self.final_verifiers
            )
        )
        return self.compose(task, results)

    @staticmethod
    def compose(
        task: TaskSpec,
        results: Sequence[VerificationResult],
        *,
        enforce_required: bool = True,
    ) -> ScoredReward:
        criteria = {criterion.id: criterion for criterion in task.criteria}
        grouped: dict[str, list[float]] = defaultdict(list)
        signals: list[RewardSignal] = []
        seen: set[tuple[str, str]] = set()

        for result in results:
            for signal in result.signals:
                if signal.criterion_id not in criteria:
                    raise ValueError(f"unknown reward criterion: {signal.criterion_id}")
                key = (signal.source, signal.criterion_id)
                if key in seen:
                    raise ValueError(
                        f"duplicate signal from {signal.source}: {signal.criterion_id}"
                    )
                seen.add(key)
                grouped[signal.criterion_id].append(signal.score)
                signals.append(signal)

        breakdown = {
            criterion_id: sum(scores) / len(scores)
            for criterion_id, scores in grouped.items()
        }
        total_weight = sum(criterion.weight for criterion in task.criteria)
        score = sum(
            criterion.weight * breakdown.get(criterion.id, 0)
            for criterion in task.criteria
        ) / total_weight

        required_failed = any(
            criterion.required and breakdown.get(criterion.id, 0) < 1
            for criterion in task.criteria
        )
        if enforce_required and required_failed:
            score = 0.0

        return ScoredReward(
            score=round(score, 6),
            signals=tuple(signals),
            force_review=any(result.force_review for result in results),
            breakdown=breakdown,
        )
