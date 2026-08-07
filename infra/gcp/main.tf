locals {
  services = toset([
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudkms.googleapis.com",
    "compute.googleapis.com",
    "container.googleapis.com",
    "firestore.googleapis.com",
    "iamcredentials.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "pubsub.googleapis.com",
    "storage.googleapis.com",
  ])

  workload_pool = "${var.project_id}.svc.id.goog"
  namespace     = "rl-env"
}

resource "google_project_service" "platform" {
  for_each = local.services

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}

resource "google_compute_network" "platform" {
  name                    = "${var.name}-network"
  auto_create_subnetworks = false

  depends_on = [google_project_service.platform["compute.googleapis.com"]]
}

resource "google_compute_subnetwork" "gke" {
  name                     = "${var.name}-gke"
  region                   = var.region
  network                  = google_compute_network.platform.id
  ip_cidr_range            = var.network_cidr
  private_ip_google_access = true

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = var.pods_cidr
  }

  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = var.services_cidr
  }
}

resource "google_compute_router" "platform" {
  name    = "${var.name}-router"
  region  = var.region
  network = google_compute_network.platform.id
}

resource "google_compute_router_nat" "platform" {
  name                               = "${var.name}-nat"
  router                             = google_compute_router.platform.name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "LIST_OF_SUBNETWORKS"

  subnetwork {
    name                    = google_compute_subnetwork.gke.id
    source_ip_ranges_to_nat = ["ALL_IP_RANGES"]
  }

  log_config {
    enable = true
    filter = "ERRORS_ONLY"
  }
}

resource "google_artifact_registry_repository" "images" {
  location      = var.region
  repository_id = "${var.name}-images"
  description   = "Digest-pinned RL environment, policy, and verifier images"
  format        = "DOCKER"
  labels        = var.labels

  cleanup_policy_dry_run = false

  cleanup_policies {
    id     = "delete-untagged"
    action = "DELETE"

    condition {
      tag_state  = "UNTAGGED"
      older_than = "2592000s"
    }
  }

  cleanup_policies {
    id     = "keep-recent"
    action = "KEEP"

    most_recent_versions {
      keep_count = 20
    }
  }

  depends_on = [google_project_service.platform["artifactregistry.googleapis.com"]]
}

resource "google_kms_key_ring" "platform" {
  name     = "${var.name}-keyring"
  location = var.region

  depends_on = [google_project_service.platform["cloudkms.googleapis.com"]]
}

resource "google_kms_crypto_key" "data" {
  name            = "${var.name}-data"
  key_ring        = google_kms_key_ring.platform.id
  rotation_period = "7776000s"

  lifecycle {
    prevent_destroy = true
  }
}

data "google_storage_project_service_account" "gcs" {
  project = var.project_id

  depends_on = [google_project_service.platform["storage.googleapis.com"]]
}

resource "google_kms_crypto_key_iam_member" "gcs" {
  crypto_key_id = google_kms_crypto_key.data.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${data.google_storage_project_service_account.gcs.email_address}"
}

resource "google_storage_bucket" "trajectories" {
  name                        = "${var.project_id}-${var.name}-trajectories"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  encryption {
    default_kms_key_name = google_kms_crypto_key.data.id
  }

  retention_policy {
    retention_period = var.trajectory_retention_days * 86400
    is_locked        = false
  }

  versioning {
    enabled = true
  }

  lifecycle_rule {
    condition {
      age = 365
    }
    action {
      type = "SetStorageClass"
      storage_class = "ARCHIVE"
    }
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

resource "google_storage_bucket" "snapshots" {
  name                        = "${var.project_id}-${var.name}-snapshots"
  location                    = var.region
  storage_class               = "STANDARD"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
  force_destroy               = false
  labels                      = var.labels

  encryption {
    default_kms_key_name = google_kms_crypto_key.data.id
  }

  versioning {
    enabled = true
  }

  depends_on = [google_kms_crypto_key_iam_member.gcs]
}

resource "google_project_service_identity" "pubsub" {
  provider = google
  project  = var.project_id
  service  = "pubsub.googleapis.com"

  depends_on = [google_project_service.platform["pubsub.googleapis.com"]]
}

resource "google_kms_crypto_key_iam_member" "pubsub" {
  crypto_key_id = google_kms_crypto_key.data.id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

resource "google_pubsub_topic" "jobs" {
  name         = "${var.name}-jobs"
  kms_key_name = google_kms_crypto_key.data.id
  labels       = var.labels

  message_retention_duration = "604800s"

  depends_on = [google_kms_crypto_key_iam_member.pubsub]
}

resource "google_pubsub_topic" "dead_letter" {
  name         = "${var.name}-dead-letter"
  kms_key_name = google_kms_crypto_key.data.id
  labels       = var.labels

  message_retention_duration = "1209600s"

  depends_on = [google_kms_crypto_key_iam_member.pubsub]
}

resource "google_pubsub_topic" "human_review" {
  name         = "${var.name}-human-review"
  kms_key_name = google_kms_crypto_key.data.id
  labels       = var.labels

  message_retention_duration = "1209600s"

  depends_on = [google_kms_crypto_key_iam_member.pubsub]
}

resource "google_pubsub_subscription" "jobs" {
  name  = "${var.name}-jobs"
  topic = google_pubsub_topic.jobs.id

  ack_deadline_seconds       = 600
  message_retention_duration = "604800s"
  retain_acked_messages      = false
  enable_exactly_once_delivery = true

  expiration_policy {
    ttl = ""
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "600s"
  }

  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }

  depends_on = [
    google_pubsub_topic_iam_member.pubsub_dead_letter_publisher,
    google_project_iam_member.pubsub_subscription_reader,
  ]
}

resource "google_pubsub_topic_iam_member" "pubsub_dead_letter_publisher" {
  topic  = google_pubsub_topic.dead_letter.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

resource "google_project_iam_member" "pubsub_subscription_reader" {
  project = var.project_id
  role    = "roles/pubsub.subscriber"
  member  = "serviceAccount:${google_project_service_identity.pubsub.email}"
}

resource "google_firestore_database" "platform" {
  project                     = var.project_id
  name                        = "(default)"
  location_id                 = var.region
  type                        = "FIRESTORE_NATIVE"
  concurrency_mode            = "OPTIMISTIC"
  app_engine_integration_mode = "DISABLED"
  deletion_policy             = var.deletion_protection ? "ABANDON" : "DELETE"

  depends_on = [google_project_service.platform["firestore.googleapis.com"]]
}

resource "google_service_account" "nodes" {
  account_id   = "${var.name}-nodes"
  display_name = "RL environment GKE nodes"
}

resource "google_service_account" "controller" {
  account_id   = "${var.name}-controller"
  display_name = "RL environment episode controller"
}

resource "google_service_account" "verifier" {
  account_id   = "${var.name}-verifier"
  display_name = "RL environment trusted verifier"
}

resource "google_project_iam_member" "node_logging" {
  project = var.project_id
  role    = "roles/logging.logWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "node_monitoring" {
  project = var.project_id
  role    = "roles/monitoring.metricWriter"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_project_iam_member" "node_registry" {
  project = var.project_id
  role    = "roles/artifactregistry.reader"
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_pubsub_topic_iam_member" "controller_publish" {
  topic  = google_pubsub_topic.jobs.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_pubsub_subscription_iam_member" "controller_consume" {
  subscription = google_pubsub_subscription.jobs.name
  role         = "roles/pubsub.subscriber"
  member       = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_pubsub_topic_iam_member" "controller_review" {
  topic  = google_pubsub_topic.human_review.name
  role   = "roles/pubsub.publisher"
  member = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_project_iam_member" "controller_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_storage_bucket_iam_member" "controller_trajectories" {
  bucket = google_storage_bucket.trajectories.name
  role   = "roles/storage.objectCreator"
  member = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_storage_bucket_iam_member" "controller_snapshots" {
  bucket = google_storage_bucket.snapshots.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.controller.email}"
}

resource "google_project_iam_member" "verifier_firestore" {
  project = var.project_id
  role    = "roles/datastore.user"
  member  = "serviceAccount:${google_service_account.verifier.email}"
}

resource "google_storage_bucket_iam_member" "verifier_trajectories" {
  bucket = google_storage_bucket.trajectories.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.verifier.email}"
}

resource "google_storage_bucket_iam_member" "verifier_snapshots" {
  bucket = google_storage_bucket.snapshots.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.verifier.email}"
}

resource "google_service_account_iam_member" "controller_workload_identity" {
  service_account_id = google_service_account.controller.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.workload_pool}[${local.namespace}/controller]"
}

resource "google_service_account_iam_member" "verifier_workload_identity" {
  service_account_id = google_service_account.verifier.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "serviceAccount:${local.workload_pool}[${local.namespace}/verifier]"
}

resource "google_container_cluster" "platform" {
  name     = "${var.name}-cluster"
  location = var.region

  network    = google_compute_network.platform.id
  subnetwork = google_compute_subnetwork.gke.id

  remove_default_node_pool = true
  initial_node_count       = 1
  deletion_protection     = var.deletion_protection

  networking_mode  = "VPC_NATIVE"
  datapath_provider = "ADVANCED_DATAPATH"
  enable_shielded_nodes = true

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false
    master_ipv4_cidr_block  = var.master_cidr
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.master_authorized_cidrs
      content {
        cidr_block   = cidr_blocks.value.cidr_block
        display_name = cidr_blocks.value.display_name
      }
    }
  }

  workload_identity_config {
    workload_pool = local.workload_pool
  }

  release_channel {
    channel = "REGULAR"
  }

  maintenance_policy {
    recurring_window {
      start_time = "2026-01-04T06:00:00Z"
      end_time   = "2026-01-04T10:00:00Z"
      recurrence = "FREQ=WEEKLY;BYDAY=SU"
    }
  }

  logging_config {
    enable_components = ["SYSTEM_COMPONENTS", "WORKLOADS"]
  }

  monitoring_config {
    enable_components = [
      "SYSTEM_COMPONENTS",
      "APISERVER",
      "SCHEDULER",
      "CONTROLLER_MANAGER",
      "STORAGE",
      "POD",
      "DEPLOYMENT",
    ]

    managed_prometheus {
      enabled = true
    }
  }

  resource_labels = var.labels

  depends_on = [
    google_project_service.platform["container.googleapis.com"],
    google_compute_router_nat.platform,
  ]
}

resource "google_container_node_pool" "system" {
  name     = "trusted-system"
  location = var.region
  cluster  = google_container_cluster.platform.name

  autoscaling {
    min_node_count = 1
    max_node_count = 3
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.system_machine_type
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      "rl-env/workload-class" = "trusted"
    }

    taint {
      key    = "rl-env/trusted"
      value  = "true"
      effect = "NO_SCHEDULE"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}

resource "google_container_node_pool" "sandbox" {
  name     = "agent-sandbox"
  location = var.region
  cluster  = google_container_cluster.platform.name

  autoscaling {
    min_node_count = 0
    max_node_count = var.sandbox_max_nodes
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }

  node_config {
    machine_type    = var.sandbox_machine_type
    image_type      = "COS_CONTAINERD"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]

    labels = {
      "rl-env/workload-class" = "sandbox"
    }

    taint {
      key    = "rl-env/sandbox"
      value  = "true"
      effect = "NO_SCHEDULE"
    }

    sandbox_config {
      type = "GVISOR"
    }

    workload_metadata_config {
      mode = "GKE_METADATA"
    }

    shielded_instance_config {
      enable_secure_boot          = true
      enable_integrity_monitoring = true
    }
  }
}
