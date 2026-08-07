"""Long-running Pub/Sub worker for GKE trusted-system nodes."""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import signal
import threading
from collections.abc import Callable, Sequence
from typing import Any

from .environments import KeyValueEnvironment
from .gcp import GCSTrajectoryStore, PubSubEventPublisher, PubSubJob, PubSubJobQueue
from .models import (
    Action,
    EnvironmentSpec,
    Observation,
    ResourceLimits,
    RewardCriterion,
    TaskSpec,
    Transition,
    as_jsonable,
)
from .rewards import RewardComposer
from .runner import EpisodeRunner
from .verifiers import AllowedActionsVerifier, StateTargetVerifier, StepProgressVerifier


LOGGER = logging.getLogger("rl_env.worker")
Handler = Callable[[dict[str, Any]], dict[str, Any]]


class ScriptedPolicy:
    """Reference policy for integration and replay jobs."""

    def __init__(self, actions: Sequence[Action]) -> None:
        self.actions = tuple(actions)

    async def act(
        self,
        task: TaskSpec,
        observation: Observation,
        trajectory: Sequence[Transition],
    ) -> Action:
        if len(trajectory) >= len(self.actions):
            return Action("submit")
        return self.actions[len(trajectory)]


def _environment_from_dict(value: dict[str, Any]) -> EnvironmentSpec:
    resources = value.get("resources", {})
    return EnvironmentSpec(
        id=value["id"],
        version=value["version"],
        image=value["image"],
        max_steps=int(value.get("max_steps", 50)),
        step_timeout_seconds=float(value.get("step_timeout_seconds", 30)),
        capabilities=tuple(value.get("capabilities", ("exec",))),
        network_allowlist=tuple(value.get("network_allowlist", ())),
        resources=ResourceLimits(**resources),
        labels=value.get("labels", {}),
    )


def _task_from_dict(value: dict[str, Any]) -> TaskSpec:
    return TaskSpec(
        id=value["id"],
        environment_revision=value["environment_revision"],
        prompt=value["prompt"],
        criteria=tuple(
            RewardCriterion(
                id=criterion["id"],
                description=criterion["description"],
                weight=float(criterion["weight"]),
                required=bool(criterion.get("required", False)),
            )
            for criterion in value["criteria"]
        ),
        initial_state=value.get("initial_state", {}),
        tags=tuple(value.get("tags", ())),
        review_band=tuple(value.get("review_band", (0.35, 0.75))),
    )


def handle_scripted_episode(payload: dict[str, Any]) -> dict[str, Any]:
    """Run a deterministic replay job and persist its trajectory to GCS."""
    environment = _environment_from_dict(payload["environment"])
    task = _task_from_dict(payload["task"])
    actions = tuple(
        Action(item["kind"], item.get("payload", {})) for item in payload["actions"]
    )
    bucket = os.environ["RL_ENV_TRAJECTORY_BUCKET"]
    allowed_actions = tuple(payload.get("allowed_actions", environment.capabilities))
    criteria = {criterion.id for criterion in task.criteria}
    final_verifiers = []
    step_verifiers = []
    if "task_success" in criteria:
        final_verifiers.append(StateTargetVerifier())
        step_verifiers.append(StepProgressVerifier())
    if "policy_compliance" in criteria:
        final_verifiers.append(AllowedActionsVerifier(allowed_actions))
    if not final_verifiers:
        raise ValueError(
            "scripted handler supports task_success and policy_compliance criteria"
        )
    runner = EpisodeRunner(
        KeyValueEnvironment,
        RewardComposer(
            final_verifiers,
            step_verifiers,
        ),
        GCSTrajectoryStore(bucket),
    )
    result = asyncio.run(
        runner.run(
            task,
            environment,
            ScriptedPolicy(actions),
            attempt=int(payload.get("attempt", 1)),
        )
    )
    return as_jsonable(result)


def load_handler(path: str) -> Handler:
    module_name, separator, attribute = path.partition(":")
    if not separator:
        raise ValueError("handler must use module:function syntax")
    handler = getattr(importlib.import_module(module_name), attribute)
    if not callable(handler):
        raise TypeError(f"handler is not callable: {path}")
    return handler


class QueueWorker:
    def __init__(
        self,
        queue: PubSubJobQueue,
        handler: Handler,
        *,
        review_publisher: PubSubEventPublisher | None = None,
        heartbeat_seconds: int = 300,
    ) -> None:
        self.queue = queue
        self.handler = handler
        self.review_publisher = review_publisher
        self.heartbeat_seconds = heartbeat_seconds
        self._stop = threading.Event()

    def stop(self, *_: object) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        while not self._stop.is_set():
            job = self.queue.claim(timeout_seconds=5)
            if job is None:
                continue
            self._process(job)

    def _process(self, job: PubSubJob) -> None:
        heartbeat_stop = threading.Event()

        def heartbeat() -> None:
            while not heartbeat_stop.wait(self.heartbeat_seconds / 2):
                self.queue.heartbeat(job, seconds=self.heartbeat_seconds)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        try:
            result = self.handler(job.payload)
            json.dumps(result)
            if (
                result.get("status") == "needs_review"
                and self.review_publisher is not None
            ):
                self.review_publisher.publish(
                    "episode.needs_review",
                    {"job_id": job.id, "result": result},
                )
            self.queue.complete(job)
            LOGGER.info(
                "job completed",
                extra={"job_id": job.id, "delivery_attempt": job.delivery_attempt},
            )
        except Exception:
            LOGGER.exception(
                "job failed",
                extra={"job_id": job.id, "delivery_attempt": job.delivery_attempt},
            )
            self.queue.fail(job)
        finally:
            heartbeat_stop.set()
            thread.join(timeout=1)


def main() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    queue = PubSubJobQueue(
        project,
        os.environ["RL_ENV_JOBS_TOPIC"],
        os.environ["RL_ENV_JOBS_SUBSCRIPTION"],
    )
    review_topic = os.getenv("RL_ENV_REVIEW_TOPIC")
    publisher = (
        PubSubEventPublisher(project, review_topic) if review_topic else None
    )
    worker = QueueWorker(
        queue,
        load_handler(
            os.getenv(
                "RL_ENV_JOB_HANDLER",
                "rl_env.worker:handle_scripted_episode",
            )
        ),
        review_publisher=publisher,
    )
    signal.signal(signal.SIGTERM, worker.stop)
    signal.signal(signal.SIGINT, worker.stop)
    worker.run_forever()


if __name__ == "__main__":
    main()
