from __future__ import annotations

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from rl_env.gcp import GCSTrajectoryStore, PubSubJob, PubSubJobQueue
from rl_env.worker import QueueWorker


class FakeBlob:
    def __init__(self, name: str) -> None:
        self.name = name
        self.metadata = None
        self.contents = b""
        self.upload_options = {}

    def upload_from_filename(self, filename, **options):
        self.contents = Path(filename).read_bytes()
        self.upload_options = options


class FakeBucket:
    def __init__(self, name: str) -> None:
        self.name = name
        self.created_blobs = []

    def blob(self, name: str):
        blob = FakeBlob(name)
        self.created_blobs.append(blob)
        return blob


class FakeStorageClient:
    def __init__(self) -> None:
        self.created_bucket = None

    def bucket(self, name: str):
        self.created_bucket = FakeBucket(name)
        return self.created_bucket


class ImmediateFuture:
    def __init__(self, value: str) -> None:
        self.value = value

    def result(self):
        return self.value


class FakePublisher:
    def __init__(self) -> None:
        self.published = []

    def topic_path(self, project: str, topic: str) -> str:
        return f"projects/{project}/topics/{topic}"

    def publish(self, topic, data, **attributes):
        self.published.append((topic, data, attributes))
        return ImmediateFuture("message-1")


class FakeSubscriber:
    def __init__(self) -> None:
        self.received_messages = []
        self.requests = []

    def subscription_path(self, project: str, subscription: str) -> str:
        return f"projects/{project}/subscriptions/{subscription}"

    def pull(self, *, request, timeout):
        self.requests.append(("pull", request, timeout))
        return SimpleNamespace(received_messages=self.received_messages)

    def acknowledge(self, *, request):
        self.requests.append(("ack", request))

    def modify_ack_deadline(self, *, request):
        self.requests.append(("deadline", request))


class GCSTrajectoryStoreTests(unittest.TestCase):
    def test_close_uploads_one_create_only_hash_chained_object(self) -> None:
        client = FakeStorageClient()
        store = GCSTrajectoryStore("trajectory-bucket", client=client)
        recorder = store.open("episode-1")
        recorder.append("episode.started", {"task": "one"})
        recorder.append("episode.completed", {"score": 1})

        recorder.close()

        bucket = client.created_bucket
        self.assertEqual(recorder.uri, "gs://trajectory-bucket/trajectories/episode-1.jsonl")
        self.assertEqual(len(bucket.created_blobs), 1)
        blob = bucket.created_blobs[0]
        self.assertEqual(blob.upload_options["if_generation_match"], 0)
        self.assertEqual(blob.upload_options["checksum"], "crc32c")
        self.assertEqual(blob.metadata["event-count"], "2")
        lines = blob.contents.decode().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[1])["previous_hash"], json.loads(lines[0])["hash"])


class PubSubJobQueueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.publisher = FakePublisher()
        self.subscriber = FakeSubscriber()
        self.queue = PubSubJobQueue(
            "project",
            "jobs",
            "jobs-sub",
            publisher=self.publisher,
            subscriber=self.subscriber,
        )

    def test_publish_claim_heartbeat_and_complete(self) -> None:
        message_id = self.queue.enqueue("job-1", {"task_id": "task-1"})
        self.assertEqual(message_id, "message-1")
        _, data, attributes = self.publisher.published[0]
        self.assertEqual(json.loads(data)["job_id"], "job-1")
        self.assertEqual(attributes["schema_version"], "1")

        self.subscriber.received_messages = [
            SimpleNamespace(
                ack_id="ack-1",
                delivery_attempt=2,
                message=SimpleNamespace(data=data),
            )
        ]
        job = self.queue.claim()
        self.assertEqual(job.id, "job-1")
        self.assertEqual(job.delivery_attempt, 2)

        self.queue.heartbeat(job, seconds=300)
        self.queue.complete(job)
        self.assertEqual(self.subscriber.requests[-2][0], "deadline")
        self.assertEqual(self.subscriber.requests[-1][0], "ack")

    def test_invalid_envelope_is_acked_and_rejected(self) -> None:
        self.subscriber.received_messages = [
            SimpleNamespace(
                ack_id="bad",
                delivery_attempt=1,
                message=SimpleNamespace(data=b'{"unexpected": true}'),
            )
        ]
        with self.assertRaisesRegex(ValueError, "invalid"):
            self.queue.claim()
        self.assertEqual(self.subscriber.requests[-1][0], "ack")


class QueueWorkerTests(unittest.TestCase):
    def test_review_result_is_published_before_ack(self) -> None:
        events = []

        class Queue:
            def complete(self, job):
                events.append(("complete", job.id))

            def fail(self, job):
                events.append(("fail", job.id))

            def heartbeat(self, job, *, seconds):
                events.append(("heartbeat", job.id))

        class ReviewPublisher:
            def publish(self, event_type, payload):
                events.append((event_type, payload["job_id"]))

        worker = QueueWorker(
            Queue(),
            lambda payload: {"status": "needs_review", "score": payload["score"]},
            review_publisher=ReviewPublisher(),
        )
        worker._process(PubSubJob("job-1", {"score": 0.5}, "ack", 1))
        self.assertEqual(
            events,
            [
                ("episode.needs_review", "job-1"),
                ("complete", "job-1"),
            ],
        )

    def test_handler_failure_nacks_job(self) -> None:
        events = []

        class Queue:
            def complete(self, job):
                events.append("complete")

            def fail(self, job):
                events.append("fail")

            def heartbeat(self, job, *, seconds):
                events.append("heartbeat")

        def fail(_):
            raise RuntimeError("handler failed")

        worker = QueueWorker(Queue(), fail)
        worker._process(PubSubJob("job-1", {}, "ack", 1))
        self.assertEqual(events, ["fail"])


if __name__ == "__main__":
    unittest.main()
