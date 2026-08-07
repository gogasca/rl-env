output "project_id" {
  value = var.project_id
}

output "artifact_registry" {
  description = "Docker registry used for policies, environments, and verifiers."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.images.repository_id}"
}

output "cluster" {
  description = "Regional GKE cluster coordinates."
  value = {
    name     = google_container_cluster.platform.name
    location = google_container_cluster.platform.location
  }
}

output "jobs_topic" {
  value = google_pubsub_topic.jobs.name
}

output "jobs_subscription" {
  value = google_pubsub_subscription.jobs.name
}

output "human_review_topic" {
  value = google_pubsub_topic.human_review.name
}

output "trajectory_bucket" {
  value = google_storage_bucket.trajectories.url
}

output "trajectory_bucket_name" {
  value = google_storage_bucket.trajectories.name
}

output "snapshot_bucket" {
  value = google_storage_bucket.snapshots.url
}

output "controller_service_account" {
  value = google_service_account.controller.email
}

output "verifier_service_account" {
  value = google_service_account.verifier.email
}

output "kubectl_credentials_command" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.platform.name} --region ${var.region} --project ${var.project_id}"
}
