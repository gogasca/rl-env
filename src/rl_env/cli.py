"""Command-line smoke test and trajectory integrity utility."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

from .environments import KeyValueEnvironment
from .models import (
    Action,
    EnvironmentSpec,
    Observation,
    RewardCriterion,
    TaskSpec,
    Transition,
    as_jsonable,
)
from .rewards import RewardComposer
from .runner import EpisodeRunner
from .store import FileTrajectoryStore
from .verifiers import AllowedActionsVerifier, StateTargetVerifier, StepProgressVerifier


class DemoPolicy:
    async def act(
        self,
        task: TaskSpec,
        observation: Observation,
        trajectory: Sequence[Transition],
    ) -> Action:
        target = dict(task.initial_state["target"])
        completed = observation.state.get("values", {})
        if isinstance(completed, dict):
            for key, value in target.items():
                if completed.get(key) != value:
                    return Action("set", {"key": key, "value": value})
        return Action("submit")


async def run_demo(output: Path) -> int:
    environment = EnvironmentSpec(
        id="customer-ops",
        version="sha256:demo-v1",
        image="in-memory://",
        max_steps=8,
        capabilities=("set", "submit"),
    )
    task = TaskSpec(
        id="route-enterprise-ticket",
        environment_revision=environment.revision,
        prompt="Set priority and owner, then submit.",
        criteria=(
            RewardCriterion("task_success", "Target state is reached", 0.8),
            RewardCriterion(
                "policy_compliance",
                "Only approved actions are used",
                0.2,
                required=True,
            ),
        ),
        initial_state={
            "values": {"priority": "normal", "owner": None},
            "target": {"priority": "urgent", "owner": "enterprise"},
        },
    )
    store = FileTrajectoryStore(output)
    runner = EpisodeRunner(
        KeyValueEnvironment,
        RewardComposer(
            (
                StateTargetVerifier(),
                AllowedActionsVerifier(("set", "submit")),
            ),
            (StepProgressVerifier(),),
        ),
        store,
    )
    result = await runner.run(task, environment, DemoPolicy())
    payload = as_jsonable(result)
    payload["trajectory_verified"] = FileTrajectoryStore.verify(
        Path(result.trajectory_uri.removeprefix("file://"))
    )
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if result.score == 1 and payload["trajectory_verified"] else 1


def main() -> None:
    parser = argparse.ArgumentParser(prog="rl-env")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo = subparsers.add_parser("demo", help="run a complete example episode")
    demo.add_argument("--output", type=Path, default=Path(".runs"))
    verify = subparsers.add_parser("verify-trajectory", help="validate a hash chain")
    verify.add_argument("path", type=Path)
    args = parser.parse_args()

    if args.command == "demo":
        raise SystemExit(asyncio.run(run_demo(args.output)))
    valid = FileTrajectoryStore.verify(args.path)
    print(json.dumps({"path": str(args.path), "valid": valid}))
    raise SystemExit(0 if valid else 1)


if __name__ == "__main__":
    main()
