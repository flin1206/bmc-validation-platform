# Hardened Ubuntu 24.04 Jenkins agent image for the firmware lab.
#
#   cd infra/packer && packer init . && packer build .
#
# The build fails if the CIS Level 1 audit of the finished image regresses,
# so the BaseOS our test results depend on is itself under test.

packer {
  required_plugins {
    qemu = {
      source  = "github.com/hashicorp/qemu"
      version = ">= 1.1.0"
    }
  }
}

variable "ubuntu_image_url" {
  type    = string
  default = "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
}

variable "ubuntu_image_checksum" {
  type    = string
  default = "file:https://cloud-images.ubuntu.com/noble/current/SHA256SUMS"
}

variable "min_hardening_index" {
  type    = number
  default = 70
}

source "qemu" "agent" {
  iso_url          = var.ubuntu_image_url
  iso_checksum     = var.ubuntu_image_checksum
  disk_image       = true
  disk_size        = "40G"
  format           = "qcow2"
  accelerator      = "kvm"
  memory           = 4096
  cpus             = 4
  headless         = true
  output_directory = "output-agent"
  vm_name          = "bmcval-agent.qcow2"

  cd_label = "cidata"
  cd_files = ["${path.root}/cloud-init/user-data", "${path.root}/cloud-init/meta-data"]

  ssh_username     = "packer"
  ssh_password     = "packer"
  ssh_timeout      = "15m"
  shutdown_command = "sudo shutdown -P now"
}

build {
  sources = ["source.qemu.agent"]

  provisioner "shell" {
    execute_command = "sudo -E bash '{{ .Path }}'"
    scripts = [
      "${path.root}/scripts/10-agent-tools.sh",
      "${path.root}/scripts/20-harden.sh",
      "${path.root}/scripts/90-audit.sh",
    ]
    environment_vars = ["MIN_HARDENING_INDEX=${var.min_hardening_index}"]
  }

  # Pull the audit evidence out of the image before it is sealed.
  provisioner "file" {
    direction   = "download"
    source      = "/var/log/bmcval-audit/"
    destination = "../../reports/baseos/"
  }

  provisioner "shell" {
    execute_command = "sudo -E bash '{{ .Path }}'"
    inline = [
      "userdel -r packer || true",   # build account must not survive into the image
      "cloud-init clean --logs",
      "truncate -s 0 /etc/machine-id",
    ]
  }
}
