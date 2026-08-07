# RL Environment Platform

A verifier-first reference platform for creating realistic agent tasks, running
them concurrently, producing dense rewards, and preserving reproducible
training trajectories.

The design turns the four environment components—task, action space, state, and
verifier—into independently versioned contracts. It is intentionally small
enough to run on a laptop while preserving the boundaries needed to move
workers to Kubernetes, Firecracker, or a managed batch system.

## What is implemented

- **Pinned environment contracts** with resource, capability, network, timeout,
  and step limits.
- **Rich task/rubric contracts** with weighted and mandatory criteria plus a
  configurable human-review band.
- **Pluggable environments** including a deterministic example and disposable
  local process workspaces.
- **Dense and final rewards** composed from independent verifiers. Unknown,
  duplicate, missing, and mandatory signals fail closed.
- **Horizontal execution semantics** through bounded concurrency and a
  lease-based, idempotent work queue with retry and expired-worker recovery.
- **Human escalation** for ambiguous scores or verifier-requested review.
- **Tamper-evident traces** containing prompts, actions, observations, rewards,
  and outcomes in a SHA-256 event chain.
- **Trusted external graders** that execute outside the agent action loop and
  return structured evidence.

## Quick start

The package has no runtime dependencies.

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e .
rl-env demo --output .runs
python -m unittest discover -s tests -v
```

The demo models an enterprise support task. Its policy updates two pieces of
state, receives intermediate progress after every action, submits the result,
and is graded for both task completion and policy compliance.

## Minimal integration

```python
from rl_env import EnvironmentSpec, RewardCriterion, TaskSpec

environment = EnvironmentSpec(
    id="salesforce",
    version="sha256:4f3...",       # immutable image or snapshot digest
    image="registry/acme/salesforce@sha256:4f3...",
    max_steps=40,
    network_allowlist=("api.internal.example",),
)

task = TaskSpec(
    id="route-renewal-risk",
    environment_revision=environment.revision,
    prompt="Review the account history and route renewal risk.",
    criteria=(
        RewardCriterion("outcome", "Correct account state", 0.6),
        RewardCriterion("process", "Required checks completed", 0.25),
        RewardCriterion("safety", "No unauthorized disclosure", 0.15, required=True),
    ),
)
```

Implement the three protocols in `rl_env.interfaces`:

1. `Environment` exposes `reset`, `step`, and `close`.
2. `Policy` selects actions from observations.
3. `FinalVerifier` and optional `StepVerifier` return rubric-scoped evidence.

Use `EpisodeRunner` for one episode, `BatchScheduler` for local concurrency, or
`SQLiteJobQueue` as a runnable lease-protocol reference for multiple workers.
At production scale, replace SQLite with PostgreSQL/SQS/Pub/Sub while keeping
job IDs, leases, attempt numbers, and completion operations idempotent.

## Security boundary

`LocalProcessEnvironment` is a development backend, not a hostile-code sandbox.
Production deployments must run each episode in an isolated pod or microVM
with:

- a read-only, digest-pinned base image and disposable copy-on-write state;
- non-root execution, seccomp/AppArmor, resource quotas, and a hard deadline;
- denied-by-default egress with explicit domain/service allowlists;
- agent-inaccessible verifier code, credentials, expected outputs, and event
  storage;
- verification in a fresh trusted sidecar or snapshot, not inside the mutable
  agent process.

See `docs/architecture.md` for the deployment design, data flow, quality loop,
and scaling model.

## Project layout

```text
src/rl_env/models.py        Versioned environment, task, action, and reward contracts
src/rl_env/environments.py  Example and local process environment backends
src/rl_env/rewards.py       Multi-verifier reward composition
src/rl_env/runner.py        Episode lifecycle and bounded batch scheduler
src/rl_env/queue.py         At-least-once lease queue reference
src/rl_env/store.py         Tamper-evident trajectory event store
src/rl_env/verifiers.py     State, policy, dense-progress, and command graders
```
