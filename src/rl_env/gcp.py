"""Google Cloud adapters for durable trajectories and distributed work."""

from __future__ import annotations

import json
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import JsonValue
from .store import TrajectoryRecorder


_SAFE_ID = re.compile(r"^[a-zA-Z0-9_.-]+$")


class GCSTrajectoryRecorder(TrajectoryRecorder):
    """Writes locally during an episode, then creates one immutable GCS object."""

    def __init__(
        self,
        bucket: Any,
        episode_id: str,
        *,
        prefix: str = "trajectories",
    ) -> None:
        self._temp_dir = Path(tempfile.mkdtemp(prefix="rl-env-gcs-"))
        self._object_name = f"{prefix.strip('/')}/{episode_id}.jsonl"
        self._bucket = bucket
        self._uploaded = False
        super().__init__(self._temp_dir / f"{episode_id}.jsonl", durable=True)

    @property
    def uri(self) -> str:
        return f"gs://{self._bucket.name}/{self._object_name}"

    def close(self) -> None:
        if self._uploaded:
            return
        super().close()
        blob = self._bucket.blob(self._object_name)
        blob.metadata = {
            "event-count": str(self._sequence),
            "final-event-sha256": self._previous_hash,
            "format": "rl-env-trajectory-v1",
        }
        try:
            blob.upload_from_filename(
                str(self.path),
                content_type="application/x-ndjson",
                if_generation_match=0,
                checksum="crc32c",
            )
            self._uploaded = True
        finally:
            shutil.rmtree(self._temp_dir, ignore_errors=True)


class GCSTrajectoryStore:
    """Trajectory store backed by a CMEK/versioned/retained GCS bucket."""

    def __init__(
        self,
        bucket_name: str,
        *,
        prefix: str = "trajectories",
        client: Any | None = None,
    ) -> None:
        if not bucket_name:
            raise ValueError("bucket name is required")
        if client is None:
            try:
                from google.cloud import storage
            except ImportError as error:
                raise RuntimeError(
                    "install the 'gcp' extra to use GCSTrajectoryStore"
                ) from error
            client = storage.Client()
        self.bucket = client.bucket(bucket_name)
        self.prefix = prefix

    def open(self, episode_id: str) -> GCSTrajectoryRecorder:
        if not _SAFE_ID.fullmatch(episode_id):
            raise ValueError("episode id contains unsafe characters")
        return GCSTrajectoryRecorder(self.bucket, episode_id, prefix=self.prefix)


@dataclass(frozen=True, slots=True)
class PubSubJob:
    id: str
    payload: dict[str, JsonValue]
    ack_id: str
    delivery_attempt: int


class PubSubJobQueue:
    """At-least-once Pub/Sub queue with explicit ack-deadline heartbeats."""

    def __init__(
        self,
        project_id: str,
        topic: str,
        subscription: str,
        *,
        publisher: Any | None = None,
        subscriber: Any | None = None,
    ) -> None:
        if not project_id or not topic or not subscription:
            raise ValueError("project, topic, and subscription are required")
        if publisher is None or subscriber is None:
            try:
                from google.cloud import pubsub_v1
            except ImportError as error:
                raise RuntimeError(
                    "install the 'gcp' extra to use PubSubJobQueue"
                ) from error
            publisher = publisher or pubsub_v1.PublisherClient()
            subscriber = subscriber or pubsub_v1.SubscriberClient()
        self.publisher = publisher
        self.subscriber = subscriber
        self.topic_path = publisher.topic_path(project_id, topic)
        self.subscription_path = subscriber.subscription_path(project_id, subscription)

    def enqueue(self, job_id: str, payload: dict[str, JsonValue]) -> str:
        if not _SAFE_ID.fullmatch(job_id):
            raise ValueError("job id contains unsafe characters")
        body = json.dumps(
            {"job_id": job_id, "payload": payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        future = self.publisher.publish(
            self.topic_path,
            body,
            job_id=job_id,
            schema_version="1",
        )
        return str(future.result())

    def claim(self, *, timeout_seconds: float = 5) -> PubSubJob | None:
        response = self.subscriber.pull(
            request={
                "subscription": self.subscription_path,
                "max_messages": 1,
            },
            timeout=timeout_seconds,
        )
        if not response.received_messages:
            return None
        received = response.received_messages[0]
        body = json.loads(received.message.data)
        if (
            not isinstance(body, dict)
            or not isinstance(body.get("job_id"), str)
            or not isinstance(body.get("payload"), dict)
        ):
            self.subscriber.acknowledge(
                request={
                    "subscription": self.subscription_path,
                    "ack_ids": [received.ack_id],
                }
            )
            raise ValueError("invalid Pub/Sub job envelope")
        return PubSubJob(
            id=body["job_id"],
            payload=body["payload"],
            ack_id=received.ack_id,
            delivery_attempt=max(int(received.delivery_attempt or 1), 1),
        )

    def heartbeat(self, job: PubSubJob, *, seconds: int = 600) -> None:
        if not 10 <= seconds <= 600:
            raise ValueError("Pub/Sub ack deadline must be between 10 and 600 seconds")
        self.subscriber.modify_ack_deadline(
            request={
                "subscription": self.subscription_path,
                "ack_ids": [job.ack_id],
                "ack_deadline_seconds": seconds,
            }
        )

    def complete(self, job: PubSubJob) -> None:
        self.subscriber.acknowledge(
            request={
                "subscription": self.subscription_path,
                "ack_ids": [job.ack_id],
            }
        )

    def fail(self, job: PubSubJob) -> None:
        self.subscriber.modify_ack_deadline(
            request={
                "subscription": self.subscription_path,
                "ack_ids": [job.ack_id],
                "ack_deadline_seconds": 0,
            }
        )


class PubSubEventPublisher:
    """Publishes human-review and operational events as canonical JSON."""

    def __init__(
        self,
        project_id: str,
        topic: str,
        *,
        publisher: Any | None = None,
    ) -> None:
        if publisher is None:
            try:
                from google.cloud import pubsub_v1
            except ImportError as error:
                raise RuntimeError(
                    "install the 'gcp' extra to use PubSubEventPublisher"
                ) from error
            publisher = pubsub_v1.PublisherClient()
        self.publisher = publisher
        self.topic_path = publisher.topic_path(project_id, topic)

    def publish(self, event_type: str, payload: dict[str, JsonValue]) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return str(
            self.publisher.publish(
                self.topic_path,
                body,
                event_type=event_type,
                schema_version="1",
            ).result()
        )
