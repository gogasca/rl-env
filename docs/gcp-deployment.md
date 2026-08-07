# Google Cloud deployment

This deployment turns the local reference implementation into a regional GCP
platform with a managed work queue, immutable trajectory storage, metadata,
container registry, and isolated worker pools.

## Deployed topology

| Concern | GCP service |
| --- | --- |
| Environment, policy, verifier images | Artifact Registry |
| Episode admission and delivery | Pub/Sub with exactly-once subscriptions |
| Poison jobs | Pub/Sub dead-letter topic |
| Human escalation | Dedicated Pub/Sub topic |
| Task/run metadata | Firestore Native |
| State snapshots | Versioned, CMEK-encrypted Cloud Storage |
| Trajectories | Versioned, retained, CMEK-encrypted Cloud Storage |
| Trusted controllers/verifiers | GKE Standard trusted node pool |
| Untrusted agent execution | Separate autoscaled GKE Sandbox/gVisor pool |
| Workload authentication | Workload Identity Federation for GKE |
| Network and audit telemetry | VPC Flow/NAT logs, GKE logs, Managed Prometheus |

The GKE cluster is regional. Nodes have private addresses, Private Google
Access, Shielded VM protections, and controlled NAT. The control-plane public
endpoint accepts only `master_authorized_cidrs`; use an empty list with a
private CI runner when possible.

## Prerequisites

- A GCP project with billing enabled.
- Terraform 1.8+, Google Cloud CLI, `kubectl`, Docker/Cloud Build access, and
  `envsubst`.
- Operator permissions to enable APIs and create IAM, networking, GKE,
  Firestore, KMS, Storage, Pub/Sub, and Artifact Registry resources.
- Application Default Credentials:

```bash
gcloud auth application-default login
gcloud auth login
```

Copy and edit the variables:

```bash
cp infra/gcp/terraform.tfvars.example infra/gcp/terraform.tfvars
```

Do not leave the example control-plane CIDR in place. Set it to an operator or
CI network, or keep the list empty and run Terraform from the VPC.

## Plan and deploy

```bash
./scripts/deploy-gcp.sh plan
./scripts/deploy-gcp.sh apply
```

`apply` provisions the infrastructure, builds the current commit with Cloud
Build, resolves the resulting image digest, deploys the trusted controller,
and waits for rollout. Images are referenced by digest, not a mutable tag.

The default Terraform state is local for bootstrap portability. Before a team
deployment, create a dedicated versioned GCS state bucket with restricted
administration and migrate state using a `gcs` backend:

```hcl
terraform {
  backend "gcs" {
    bucket = "YOUR_TERRAFORM_STATE_BUCKET"
    prefix = "rl-env/production"
  }
}
```

Never store Terraform state in the trajectory or snapshot bucket because state
contains infrastructure metadata and has a different access lifecycle.

## Submit a replay episode

Install the GCP extra and use the example:

```bash
python -m pip install -e '.[gcp]'
export GOOGLE_CLOUD_PROJECT="$(terraform -chdir=infra/gcp output -raw project_id)"
export RL_ENV_JOBS_TOPIC="$(terraform -chdir=infra/gcp output -raw jobs_topic)"
python examples/gcp_submit.py
```

The reference handler executes deterministic action replays. It is useful for
environment QA, expert-trace replay, and deployment smoke tests. Production
policy images should provide their own handler through
`RL_ENV_JOB_HANDLER=package.module:function`.

The handler contract is:

```python
def handle(payload: dict) -> dict:
    # Return a JSON-serializable episode result.
    ...
```

Pub/Sub can redeliver a message despite exactly-once mode during exceptional
conditions. The `job_id` is therefore an idempotency key. Production handlers
must conditionally create a Firestore run record before allocating an
environment and return the committed result when that record already exists.

## Untrusted agent episodes

`infra/gcp/kubernetes/episode-job.yaml.tpl` is the hardened job contract for
arbitrary agent code:

- runs only on the gVisor node pool;
- has no Google service account or Kubernetes token;
- drops Linux capabilities, forbids privilege escalation, uses seccomp, and
  has a read-only root filesystem;
- receives bounded CPU, memory, disk, and wall-clock resources;
- is denied network access except DNS and Google's restricted API VIP;
- writes only to a per-episode object through a short-lived signed URL.

The controller must create the signed upload URL for a unique object name,
create the Secret and Job, wait for completion, then delete the Secret. Never
put hidden tests, verifier credentials, expected outputs, or broad bucket
credentials into this pod.

After the agent result is frozen,
`infra/gcp/kubernetes/verifier-job.yaml.tpl` runs a digest-pinned verifier on
the trusted pool. Its Workload Identity can read snapshots and trajectories
and update Firestore, while the agent identity cannot. Domain-specific
controllers should render these two templates or create equivalent Jobs
through the Kubernetes API.

## Identity model

- **Node service account:** image pulls, logs, and metrics only.
- **Controller service account:** consume/publish jobs, create trajectory
  objects, read snapshots, and update Firestore.
- **Verifier service account:** read snapshots/trajectories and update
  verification records.
- **Agent Kubernetes service account:** no cloud IAM binding and token
  automount disabled.

No static service-account keys are created.

## Operations

Monitor these signals and alert on sustained changes:

- oldest unacked Pub/Sub message age and dead-letter count;
- GKE unschedulable sandbox pods and sandbox node-pool saturation;
- episode infrastructure-error rate by environment digest;
- verifier latency, disagreement, and missing rubric signals;
- human-review backlog and reviewer overturn rate;
- GCS upload precondition failures, which indicate duplicate episode IDs;
- unexpected agent egress denies and IAM access denials.

The controller HPA uses CPU as a safe default. For queue-driven production
scaling, install a Pub/Sub external metric adapter or KEDA and scale on oldest
unacked message age, while keeping node autoscaling enabled.

## Production hardening checklist

- Lock the trajectory bucket retention policy after validating retention.
- Add Organization Policy constraints for public IPs, service-account keys,
  allowed regions, and trusted images.
- Enable Binary Authorization and require signed, vulnerability-scanned image
  attestations.
- Route all agent egress through a policy-aware proxy when tasks need external
  systems; do not replace deny-by-default with unrestricted internet.
- Put the GKE control-plane endpoint behind private access for production.
- Export Admin Activity, Data Access, GKE, KMS, and Storage logs to a separate
  security project.
- Use separate projects for development, task authoring, and production
  execution, with independently managed KMS keys.
- Test restore, replay, dead-letter reprocessing, key rotation, and regional
  recovery before accepting training-critical workloads.
