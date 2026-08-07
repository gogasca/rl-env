# Architecture: RL environments at scale

## Design goals

The system optimizes for transfer to real work, useful learning signal over
long horizons, reproducibility, and verifier integrity. Raw episode throughput
is not enough: a cheap unrealistic environment or exploitable grader scales the
wrong behavior.

Every result is therefore keyed by:

```text
(task version, environment snapshot digest, verifier version, policy version, seed)
```

The current Python contracts cover the first two directly. A production
registry should require all five before admitting a job.

## Components

```text
Task authoring + SME review
          |
          v
 Task/rubric registry ---- Environment/image registry
          |                         |
          +------------+------------+
                       v
             Admission + job queue
                       |
             +---------+---------+
             |                   |
             v                   v
       Episode worker N     Episode worker N+1
       isolated sandbox     isolated sandbox
             |                   |
             +---------+---------+
                       v
              Trusted verifier pool
                       |
             +---------+---------+
             |                   |
             v                   v
     Trajectory object store   Metrics/QA DB
                                 |
                                 v
                         Human review queue
```

### Control plane

The control plane stores immutable task revisions, rubric revisions, image
digests, verifier bundles, worker capabilities, and dataset splits. Admission
rejects unpinned environments, criteria without graders, incompatible action
spaces, and jobs exceeding an operator policy.

The queue provides at-least-once delivery. Job IDs are deterministic hashes of
the evaluation tuple, workers hold renewable leases, and result commits are
idempotent. `SQLiteJobQueue` implements these semantics for one host. A
production adapter uses PostgreSQL `FOR UPDATE SKIP LOCKED`, SQS, Pub/Sub, or
Kafka and places large payloads in object storage.

### Data plane

One episode runs in one disposable sandbox:

1. Pull a digest-pinned image and restore a versioned state snapshot.
2. Materialize task-specific, non-secret state.
3. Run the policy/action loop under step and episode budgets.
4. Stream every action and observation to append-only storage.
5. Freeze the resulting state.
6. Evaluate it in a trusted verifier boundary.
7. Commit the score, evidence, and trajectory URI atomically.
8. Destroy the sandbox.

Workers are stateless and scale on queue age, runnable job count, and requested
CPU/GPU/memory class. Warm pools and layered snapshots reduce startup latency;
they must never reuse writable state across episodes.

### Reward plane

A rubric is the contract between domain intent and optimization. Each criterion
has a positive weight, evidence, and optional mandatory status. Independent
graders may include:

- deterministic state assertions and test suites;
- process checks over actions and intermediate states;
- differential checks against a clean reference system;
- model judges calibrated against expert-labeled examples;
- human expert review for ambiguous or high-risk outcomes.

Scores are normalized to `[0, 1]`. Signals are averaged per criterion, then
weighted across the rubric. A mandatory criterion below full credit sets the
episode score to zero. Unknown criteria and repeated source/criterion pairs are
errors. This prevents graders from changing the objective or inflating a score
by emitting duplicate evidence.

Intermediate verifiers provide dense learning signal but do not prescribe one
gold path. They should score invariant progress—valid records created,
constraints satisfied, tests newly passing—rather than exact action sequences.

## Reward-hacking defenses

Verifier code, hidden tests, credentials, reference outputs, and reward events
must not be mounted into the agent sandbox. Verification runs after state is
frozen, ideally against a fresh copy. The platform also:

- caps and validates every signal;
- uses multiple independent evidence sources for high-value criteria;
- keeps canary tasks and hidden perturbations out of the training split;
- compares claimed reasoning with observed actions where traces include notes;
- flags unusual score/action-length, file-access, and tool-use distributions;
- rotates adversarial tasks after exploits are discovered;
- samples high-confidence passes as well as failures for expert audit.

No metric eliminates reward hacking. The operational loop—red-team, audit,
patch verifier, replay historical trajectories—is part of the reward system.

## Task and quality lifecycle

1. **Source:** an SME derives a task from a real workflow and removes sensitive
   data.
2. **Specify:** define initial state, allowed capabilities, invariants, rubric,
   evidence, and multiple valid solution paths.
3. **Reproduce:** a second expert completes the task from the pinned snapshot.
4. **Validate:** automated checks replay setup and graders; another expert
   reviews task-verifier alignment.
5. **Calibrate:** run baseline and frontier policies. Reject impossible,
   trivial, flaky, leaking, or saturated tasks.
6. **Publish:** freeze task, image, and verifier revisions; assign train,
   validation, hidden test, and canary splits.
7. **Monitor:** track success, score variance, grader disagreement, review
   overturn rate, reward/action correlation, infra failures, and saturation.
8. **Refresh:** retire saturated tasks and add harder variants without mutating
   published revisions.

Task difficulty should target a useful success band for the current policy.
Curriculum selection samples just above current capability while retaining
coverage over domains, tool combinations, long-horizon lengths, and rare edge
cases.

## Reliability and observability

Infrastructure failures are distinct from valid zero rewards and may be
retried. Task failures are not retried automatically. Essential metrics:

- queue depth/age and lease expiration rate;
- image restore, reset, action, verifier, and total episode latency;
- infrastructure failure rate by image and worker class;
- reward distribution and criterion-level missing/zero rates;
- verifier disagreement and human-review overturn rate;
- environment determinism under replay;
- task saturation and policy-version regressions.

Trajectory events use a hash chain in this implementation. At scale, workers
write chunks to immutable object storage, sign a manifest, and index only
metadata and URIs in the operational database. Retention and access policies
must account for potentially sensitive prompts, files, and expert traces.

## Scaling path

| Stage | Execution | Queue/state | Trajectories | Verification |
| --- | --- | --- | --- | --- |
| Developer | `BatchScheduler` | memory | local JSONL | in process |
| Single host | processes/containers | `SQLiteJobQueue` | local/S3-compatible | separate process |
| Cluster | GKE trusted + gVisor pools | Pub/Sub + Firestore | retained CMEK GCS | isolated verifier jobs |
| Multi-region | region-local worker pools | globally routed jobs | replicated object store | pinned regional bundles |

Capacity scales approximately with arrival rate multiplied by p95 episode
duration, divided by target utilization. Separate worker pools by resource
shape and environment family to avoid head-of-line blocking. Apply per-tenant
quotas and weighted fair scheduling before autoscaling.

## Deliberate limitations of the reference implementation

- The local process backend does not enforce OS isolation or network policy.
- SQLite coordinates workers on one shared host, not across regions.
- Contracts are Python dataclasses rather than a network API or schema
  registry.
- Human-review assignment and model-judge calibration are represented by
  routing semantics, not a user interface.

These are replaceable adapters around stable task, episode, reward, and
trajectory contracts; they are not hidden assumptions in the action loop. The
GCP deployment in `docs/gcp-deployment.md` replaces local execution/storage
with Pub/Sub, retained CMEK-encrypted GCS, Firestore, Workload Identity, and
separate trusted and gVisor GKE pools.
