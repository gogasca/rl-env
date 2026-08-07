from __future__ import annotations

import json
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path

from rl_env import (
    Action,
    BatchScheduler,
    EnvironmentSpec,
    EpisodeRunner,
    EpisodeStatus,
    FileTrajectoryStore,
    KeyValueEnvironment,
    Observation,
    RewardComposer,
    RewardCriterion,
    RewardSignal,
    StepOutcome,
    TaskSpec,
    Transition,
    VerificationResult,
)
from rl_env.verifiers import StateTargetVerifier, StepProgressVerifier
from rl_env.queue import SQLiteJobQueue


ENVIRONMENT = EnvironmentSpec(
    id="test",
    version="v1",
    image="in-memory://",
    max_steps=5,
)


def make_task(task_id: str = "task") -> TaskSpec:
    return TaskSpec(
        id=task_id,
        environment_revision=ENVIRONMENT.revision,
        prompt="Set status to done",
        criteria=(RewardCriterion("task_success", "Correct final state", 1),),
        initial_state={
            "values": {"status": "open"},
            "target": {"status": "done"},
        },
    )


class CompletingPolicy:
    async def act(
        self,
        task: TaskSpec,
        observation: Observation,
        trajectory: Sequence[Transition],
    ) -> Action:
        if not trajectory:
            return Action("set", {"key": "status", "value": "done"})
        return Action("submit")


class StaticVerifier:
    name = "static"

    def __init__(self, *signals: RewardSignal, force_review: bool = False) -> None:
        self.signals = signals
        self.force_review = force_review

    async def evaluate(self, task, final_observation, trajectory):
        return VerificationResult(self.signals, force_review=self.force_review)


class ModelAndRewardTests(unittest.TestCase):
    def test_task_requires_pinned_environment(self) -> None:
        with self.assertRaisesRegex(ValueError, "pinned"):
            TaskSpec(
                id="bad",
                environment_revision="latest",
                prompt="bad",
                criteria=(RewardCriterion("result", "Result", 1),),
            )

    def test_required_criterion_fails_closed(self) -> None:
        task = TaskSpec(
            id="required",
            environment_revision=ENVIRONMENT.revision,
            prompt="test",
            criteria=(
                RewardCriterion("quality", "Quality", 3),
                RewardCriterion("safety", "Safety", 1, required=True),
            ),
        )
        composed = RewardComposer.compose(
            task,
            (
                VerificationResult(
                    (
                        RewardSignal("quality", 1, "good", "judge"),
                        RewardSignal("safety", 0.5, "uncertain", "policy"),
                    )
                ),
            ),
        )
        self.assertEqual(composed.score, 0)

    def test_duplicate_source_signal_is_rejected(self) -> None:
        task = make_task()
        duplicate = RewardSignal("task_success", 1, "same", "judge")
        with self.assertRaisesRegex(ValueError, "duplicate"):
            RewardComposer.compose(
                task, (VerificationResult((duplicate, duplicate)),)
            )


class EpisodeIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dense_reward_does_not_apply_final_required_gate(self) -> None:
        task = TaskSpec(
            id="dense",
            environment_revision=ENVIRONMENT.revision,
            prompt="test",
            criteria=(
                RewardCriterion("progress", "Intermediate progress", 3),
                RewardCriterion("safety", "Final safety check", 1, required=True),
            ),
        )

        class ProgressVerifier:
            name = "progress"

            async def evaluate_step(self, task, transition, trajectory):
                return VerificationResult(
                    (RewardSignal("progress", 1, "milestone reached", self.name),)
                )

        composer = RewardComposer(
            (StaticVerifier(RewardSignal("safety", 1, "safe", "final")),),
            (ProgressVerifier(),),
        )
        transition = Transition(
            0,
            Action("work"),
            outcome=StepOutcome(Observation("worked")),
        )
        scored = await composer.score_step(task, transition, (transition,))
        self.assertEqual(scored.score, 0.75)

    async def test_episode_has_dense_rewards_and_verified_trace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileTrajectoryStore(directory)
            runner = EpisodeRunner(
                KeyValueEnvironment,
                RewardComposer(
                    (StateTargetVerifier(),),
                    (StepProgressVerifier(),),
                ),
                store,
            )

            result = await runner.run(make_task(), ENVIRONMENT, CompletingPolicy())

            self.assertEqual(result.status, EpisodeStatus.SUCCEEDED)
            self.assertEqual(result.score, 1)
            self.assertEqual([step.reward for step in result.transitions], [1, 1])
            trace = Path(result.trajectory_uri.removeprefix("file://"))
            self.assertTrue(FileTrajectoryStore.verify(trace))
            events = [json.loads(line)["type"] for line in trace.read_text().splitlines()]
            self.assertEqual(
                events,
                [
                    "episode.started",
                    "environment.reset",
                    "agent.action",
                    "environment.transition",
                    "agent.action",
                    "environment.transition",
                    "episode.completed",
                ],
            )

    async def test_ambiguous_score_routes_to_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            runner = EpisodeRunner(
                KeyValueEnvironment,
                RewardComposer(
                    (
                        StaticVerifier(
                            RewardSignal(
                                "task_success", 0.5, "ambiguous", "llm_judge"
                            )
                        ),
                    )
                ),
                FileTrajectoryStore(directory),
            )
            result = await runner.run(make_task(), ENVIRONMENT, CompletingPolicy())
            self.assertEqual(result.status, EpisodeStatus.NEEDS_REVIEW)

    async def test_scheduler_retries_only_infrastructure_failure(self) -> None:
        attempts = 0

        class FlakyEnvironment(KeyValueEnvironment):
            async def reset(self, task):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise RuntimeError("transient worker loss")
                return await super().reset(task)

        with tempfile.TemporaryDirectory() as directory:
            runner = EpisodeRunner(
                FlakyEnvironment,
                RewardComposer((StateTargetVerifier(),)),
                FileTrajectoryStore(directory),
            )
            scheduler = BatchScheduler(runner, max_concurrency=2, max_attempts=2)
            results = await scheduler.run(
                ((make_task("retry"), ENVIRONMENT),),
                lambda _: CompletingPolicy(),
            )
            self.assertEqual(attempts, 2)
            self.assertEqual(results[0].status, EpisodeStatus.SUCCEEDED)


class TrajectoryStoreTests(unittest.TestCase):
    def test_tampering_breaks_hash_chain(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            store = FileTrajectoryStore(directory)
            recorder = store.open("episode")
            recorder.append("one", {"value": 1})
            recorder.append("two", {"value": 2})
            recorder.close()
            self.assertTrue(store.verify(recorder.path))

            lines = recorder.path.read_text().splitlines()
            event = json.loads(lines[0])
            event["payload"]["value"] = 999
            lines[0] = json.dumps(event)
            recorder.path.write_text("\n".join(lines) + "\n")
            self.assertFalse(store.verify(recorder.path))


class JobQueueTests(unittest.TestCase):
    def test_idempotency_and_expired_lease_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            queue = SQLiteJobQueue(Path(directory) / "jobs.db", max_attempts=2)
            self.assertTrue(queue.enqueue("job-1", {"task_id": "task"}))
            self.assertFalse(queue.enqueue("job-1", {"task_id": "other"}))

            first = queue.claim("worker-a", lease_seconds=10, now=100)
            self.assertIsNotNone(first)
            self.assertIsNone(queue.claim("worker-b", now=105))
            recovered = queue.claim("worker-b", now=111)
            self.assertIsNotNone(recovered)
            assert first is not None and recovered is not None
            self.assertEqual(recovered.attempt, 2)
            self.assertFalse(queue.complete(first, {"stale": True}))
            self.assertTrue(queue.complete(recovered, {"score": 1}))
            self.assertEqual(queue.get("job-1")["status"], "completed")


if __name__ == "__main__":
    unittest.main()
