"""Submit a complete replay episode to the GCP worker queue."""

from __future__ import annotations

import os
import uuid

from rl_env.gcp import PubSubJobQueue


def main() -> None:
    project = os.environ["GOOGLE_CLOUD_PROJECT"]
    topic = os.environ.get("RL_ENV_JOBS_TOPIC", "rl-env-jobs")
    subscription = os.environ.get("RL_ENV_JOBS_SUBSCRIPTION", "rl-env-jobs")
    queue = PubSubJobQueue(project, topic, subscription)
    job_id = f"customer-ops-{uuid.uuid4().hex[:12]}"

    payload = {
        "environment": {
            "id": "customer-ops",
            "version": "sha256:demo-v1",
            "image": "in-memory://",
            "max_steps": 8,
            "capabilities": ["set", "submit"],
        },
        "task": {
            "id": "route-enterprise-ticket",
            "environment_revision": "customer-ops@sha256:demo-v1",
            "prompt": "Set priority and owner, then submit.",
            "criteria": [
                {
                    "id": "task_success",
                    "description": "Target state is reached",
                    "weight": 0.8,
                },
                {
                    "id": "policy_compliance",
                    "description": "Only approved actions are used",
                    "weight": 0.2,
                    "required": True,
                },
            ],
            "initial_state": {
                "values": {"priority": "normal", "owner": None},
                "target": {"priority": "urgent", "owner": "enterprise"},
            },
        },
        "actions": [
            {
                "kind": "set",
                "payload": {"key": "priority", "value": "urgent"},
            },
            {
                "kind": "set",
                "payload": {"key": "owner", "value": "enterprise"},
            },
            {"kind": "submit"},
        ],
        "allowed_actions": ["set", "submit"],
    }
    message_id = queue.enqueue(job_id, payload)
    print(f"submitted job_id={job_id} message_id={message_id}")


if __name__ == "__main__":
    main()
