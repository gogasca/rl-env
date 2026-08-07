#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TF_DIR="${ROOT}/infra/gcp"
MODE="${1:-plan}"

for command in terraform gcloud kubectl; do
  command -v "${command}" >/dev/null || {
    echo "Missing required command: ${command}" >&2
    exit 1
  }
done

terraform -chdir="${TF_DIR}" init
terraform -chdir="${TF_DIR}" validate

if [[ "${MODE}" == "plan" ]]; then
  terraform -chdir="${TF_DIR}" plan
  exit 0
fi

if [[ "${MODE}" != "apply" ]]; then
  echo "Usage: $0 [plan|apply]" >&2
  exit 2
fi

terraform -chdir="${TF_DIR}" apply

PROJECT_ID="$(terraform -chdir="${TF_DIR}" output -raw project_id)"
REGISTRY="$(terraform -chdir="${TF_DIR}" output -raw artifact_registry)"
CLUSTER_NAME="$(terraform -chdir="${TF_DIR}" output -json cluster | python -c 'import json,sys; print(json.load(sys.stdin)["name"])')"
CLUSTER_LOCATION="$(terraform -chdir="${TF_DIR}" output -json cluster | python -c 'import json,sys; print(json.load(sys.stdin)["location"])')"
TAG="$(git -C "${ROOT}" rev-parse --short=12 HEAD)"
IMAGE_TAG="${REGISTRY}/controller:${TAG}"

gcloud builds submit "${ROOT}" --project "${PROJECT_ID}" --tag "${IMAGE_TAG}"
DIGEST="$(gcloud artifacts docker images describe "${IMAGE_TAG}" \
  --project "${PROJECT_ID}" \
  --format='value(image_summary.digest)')"
export CONTROLLER_IMAGE="${REGISTRY}/controller@${DIGEST}"

gcloud container clusters get-credentials "${CLUSTER_NAME}" \
  --region "${CLUSTER_LOCATION}" \
  --project "${PROJECT_ID}"

export PROJECT_ID
export CONTROLLER_GSA
CONTROLLER_GSA="$(terraform -chdir="${TF_DIR}" output -raw controller_service_account)"
export VERIFIER_GSA
VERIFIER_GSA="$(terraform -chdir="${TF_DIR}" output -raw verifier_service_account)"
export JOBS_TOPIC
JOBS_TOPIC="$(terraform -chdir="${TF_DIR}" output -raw jobs_topic)"
export JOBS_SUBSCRIPTION
JOBS_SUBSCRIPTION="$(terraform -chdir="${TF_DIR}" output -raw jobs_subscription)"
export REVIEW_TOPIC
REVIEW_TOPIC="$(terraform -chdir="${TF_DIR}" output -raw human_review_topic)"
export TRAJECTORY_BUCKET
TRAJECTORY_BUCKET="$(terraform -chdir="${TF_DIR}" output -raw trajectory_bucket_name)"

command -v envsubst >/dev/null || {
  echo "envsubst is required to render Kubernetes manifests" >&2
  exit 1
}

RENDERED="$(mktemp)"
trap 'rm -f "${RENDERED}"' EXIT
envsubst <"${TF_DIR}/kubernetes/platform.yaml.tpl" >"${RENDERED}"
kubectl apply -f "${RENDERED}"
kubectl -n rl-env rollout status deployment/controller --timeout=5m

echo "GCP RL environment platform deployed with ${CONTROLLER_IMAGE}"
