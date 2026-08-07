"""Append-only, tamper-evident trajectory storage."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import JsonValue, as_jsonable


class TrajectoryRecorder:
    def __init__(self, path: Path, durable: bool = False) -> None:
        self.path = path
        self._durable = durable
        self._sequence = 0
        self._previous_hash = "0" * 64
        self._handle = path.open("x", encoding="utf-8")

    @property
    def uri(self) -> str:
        return self.path.resolve().as_uri()

    def append(self, event_type: str, payload: Any) -> str:
        if not event_type:
            raise ValueError("event type is required")
        body: dict[str, JsonValue] = {
            "sequence": self._sequence,
            "timestamp": datetime.now(UTC).isoformat(),
            "type": event_type,
            "payload": as_jsonable(payload),
            "previous_hash": self._previous_hash,
        }
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
        event_hash = hashlib.sha256(canonical.encode()).hexdigest()
        body["hash"] = event_hash
        self._handle.write(json.dumps(body, sort_keys=True) + "\n")
        self._handle.flush()
        if self._durable:
            os.fsync(self._handle.fileno())
        self._sequence += 1
        self._previous_hash = event_hash
        return event_hash

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    def __enter__(self) -> "TrajectoryRecorder":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


class FileTrajectoryStore:
    """Filesystem backend; object-store implementations can share this contract."""

    _SAFE_ID = re.compile(r"^[a-zA-Z0-9_.-]+$")

    def __init__(self, root: str | Path, durable: bool = False) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.durable = durable

    def open(self, episode_id: str) -> TrajectoryRecorder:
        if not self._SAFE_ID.fullmatch(episode_id):
            raise ValueError("episode id contains unsafe characters")
        return TrajectoryRecorder(self.root / f"{episode_id}.jsonl", self.durable)

    @staticmethod
    def verify(path: str | Path) -> bool:
        previous_hash = "0" * 64
        expected_sequence = 0
        with Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                event = json.loads(line)
                claimed_hash = event.pop("hash")
                if (
                    event["sequence"] != expected_sequence
                    or event["previous_hash"] != previous_hash
                ):
                    return False
                canonical = json.dumps(event, sort_keys=True, separators=(",", ":"))
                actual_hash = hashlib.sha256(canonical.encode()).hexdigest()
                if actual_hash != claimed_hash:
                    return False
                expected_sequence += 1
                previous_hash = claimed_hash
        return expected_sequence > 0
