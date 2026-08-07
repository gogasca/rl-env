variable "project_id" {
  description = "GCP project that owns the RL environment platform."
  type        = string
}

variable "region" {
  description = "Primary GCP region."
  type        = string
  default     = "us-central1"
}

variable "name" {
  description = "Short resource-name prefix."
  type        = string
  default     = "rl-env"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}[a-z0-9]$", var.name))
    error_message = "name must be 3-22 lowercase letters, numbers, or hyphens."
  }
}

variable "network_cidr" {
  description = "Primary subnet range for GKE nodes."
  type        = string
  default     = "10.20.0.0/20"
}

variable "pods_cidr" {
  description = "Secondary subnet range for Kubernetes pods."
  type        = string
  default     = "10.24.0.0/14"
}

variable "services_cidr" {
  description = "Secondary subnet range for Kubernetes services."
  type        = string
  default     = "10.28.0.0/20"
}

variable "master_cidr" {
  description = "Private GKE control-plane range."
  type        = string
  default     = "172.16.0.0/28"
}

variable "master_authorized_cidrs" {
  description = "Networks allowed to reach the public GKE control-plane endpoint."
  type = list(object({
    cidr_block   = string
    display_name = string
  }))
  default = []
}

variable "system_machine_type" {
  description = "Machine type for trusted controllers and verifiers."
  type        = string
  default     = "e2-standard-4"
}

variable "sandbox_machine_type" {
  description = "Machine type for untrusted agent episode pods."
  type        = string
  default     = "n2-standard-4"
}

variable "sandbox_max_nodes" {
  description = "Maximum sandbox nodes per zone."
  type        = number
  default     = 20

  validation {
    condition     = var.sandbox_max_nodes >= 1
    error_message = "sandbox_max_nodes must be at least one."
  }
}

variable "trajectory_retention_days" {
  description = "Minimum immutable trajectory retention period."
  type        = number
  default     = 30

  validation {
    condition     = var.trajectory_retention_days >= 1
    error_message = "trajectory retention must be at least one day."
  }
}

variable "deletion_protection" {
  description = "Protect the GKE cluster and Firestore database from deletion."
  type        = bool
  default     = true
}

variable "labels" {
  description = "Labels applied to supported resources."
  type        = map(string)
  default = {
    application = "rl-env"
    managed-by  = "terraform"
  }
}
