"""Validated contracts shared by environment, runner, and verifier processes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from types import MappingProxyType
from typing import Any, Mapping, Sequence


JsonValue = None | bool | int | float | str | list["JsonValue"] | dict[str, "JsonValue"]


class EpisodeStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"
    INFRA_ERROR = "infra_error"


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(value))


@dataclass(frozen=True, slots=True)
class ResourceLimits:
    cpu: float = 1.0
    memory_mb: int = 1024
    disk_mb: int = 2048

    def __post_init__(self) -> None:
        if self.cpu <= 0 or self.memory_mb <= 0 or self.disk_mb <= 0:
            raise ValueError("resource limits must be positive")


@dataclass(frozen=True, slots=True)
class EnvironmentSpec:
    """A versioned, reproducible environment definition."""

    id: str
    version: str
    image: str
    max_steps: int = 50
    step_timeout_seconds: float = 30.0
    capabilities: tuple[str, ...] = ("exec",)
    network_allowlist: tuple[str, ...] = ()
    resources: ResourceLimits = field(default_factory=ResourceLimits)
    labels: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not self.version or not self.image:
            raise ValueError("environment id, version, and image are required")
        if self.max_steps < 1 or self.step_timeout_seconds <= 0:
            raise ValueError("max_steps and step timeout must be positive")
        object.__setattr__(self, "labels", _frozen_mapping(self.labels))

    @property
    def revision(self) -> str:
        return f"{self.id}@{self.version}"


@dataclass(frozen=True, slots=True)
class RewardCriterion:
    id: str
    description: str
    weight: float
    required: bool = False

    def __post_init__(self) -> None:
        if not self.id or not self.description:
            raise ValueError("criterion id and description are required")
        if self.weight <= 0:
            raise ValueError("criterion weight must be positive")


@dataclass(frozen=True, slots=True)
class TaskSpec:
    id: str
    environment_revision: str
    prompt: str
    criteria: tuple[RewardCriterion, ...]
    initial_state: Mapping[str, JsonValue] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    review_band: tuple[float, float] = (0.35, 0.75)

    def __post_init__(self) -> None:
        if not self.id or not self.prompt or "@" not in self.environment_revision:
            raise ValueError("task id, prompt, and a pinned environment revision are required")
        criterion_ids = [item.id for item in self.criteria]
        if not criterion_ids or len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("tasks require uniquely named reward criteria")
        low, high = self.review_band
        if not 0 <= low <= high <= 1:
            raise ValueError("review band must be within [0, 1]")
        object.__setattr__(self, "initial_state", _frozen_mapping(self.initial_state))


@dataclass(frozen=True, slots=True)
class Action:
    kind: str
    payload: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.kind:
            raise ValueError("action kind is required")
        object.__setattr__(self, "payload", _frozen_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class Observation:
    message: str
    state: Mapping[str, JsonValue] = field(default_factory=dict)
    artifacts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "state", _frozen_mapping(self.state))


@dataclass(frozen=True, slots=True)
class StepOutcome:
    observation: Observation
    terminated: bool = False
    truncated: bool = False


@dataclass(frozen=True, slots=True)
class Transition:
    index: int
    action: Action
    outcome: StepOutcome
    reward: float = 0.0


@dataclass(frozen=True, slots=True)
class RewardSignal:
    criterion_id: str
    score: float
    evidence: str
    source: str

    def __post_init__(self) -> None:
        if not 0 <= self.score <= 1:
            raise ValueError("signal score must be within [0, 1]")
        if not self.source:
            raise ValueError("signal source is required")


@dataclass(frozen=True, slots=True)
class VerificationResult:
    signals: tuple[RewardSignal, ...]
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)
    force_review: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _frozen_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class EpisodeResult:
    episode_id: str
    task_id: str
    environment_revision: str
    status: EpisodeStatus
    score: float
    transitions: tuple[Transition, ...]
    signals: tuple[RewardSignal, ...]
    trajectory_uri: str
    error: str | None = None

    @property
    def steps(self) -> int:
        return len(self.transitions)


def as_jsonable(value: Any) -> JsonValue:
    """Convert platform dataclasses and enums into stable JSON-compatible data."""
    if isinstance(value, Enum):
        return value.value
    if hasattr(value, "__dataclass_fields__"):
        return {
            name: as_jsonable(getattr(value, name))
            for name in value.__dataclass_fields__
        }
    if isinstance(value, Mapping):
        return {str(key): as_jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list, Sequence)) and not isinstance(value, (str, bytes)):
        return [as_jsonable(item) for item in value]
    return value
