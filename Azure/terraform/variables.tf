# variables.tf - Input variables for Azure deployment

variable "location" {
  description = "Azure region for deployment"
  type        = string
  default     = "eastus"
}

variable "resource_group_name" {
  description = "Azure Resource Group name"
  type        = string
  default     = "redStack-rg"
}

variable "project_name" {
  description = "Project name for resource tagging"
  type        = string
  default     = "redStack"
}

variable "your_public_ip" {
  description = "Your public IP for SSH/management access (CIDR format, e.g., 1.2.3.4/32)"
  type        = string
}

variable "ssh_public_key_path" {
  description = "Path to your SSH public key file (e.g., ~/.ssh/id_rsa.pub)"
  type        = string
  default     = "~/.ssh/redstack-key.pub"
}

variable "admin_password" {
  description = "Administrator password for all VMs (Linux admin user + Windows Administrator)"
  type        = string
  sensitive   = true
}

variable "mythic_vm_size" {
  description = "Azure VM size for Mythic team server"
  type        = string
  default     = "Standard_B2s"
}

variable "guacamole_vm_size" {
  description = "Azure VM size for Guacamole server"
  type        = string
  default     = "Standard_B1s"
}

variable "windows_vm_size" {
  description = "Azure VM size for Windows client"
  type        = string
  default     = "Standard_B2s"
}

variable "redirector_vm_size" {
  description = "Azure VM size for Apache redirector"
  type        = string
  default     = "Standard_B1s"
}

variable "sliver_vm_size" {
  description = "Azure VM size for Sliver C2 server"
  type        = string
  default     = "Standard_B1s"
}

variable "havoc_vm_size" {
  description = "Azure VM size for Havoc C2 server"
  type        = string
  default     = "Standard_B2s"
}

variable "kali_deployment_mode" {
  description = "Kali mode: 'headless' (SSH only) or 'gui' (XFCE + XRDP)"
  type        = string
  default     = "headless"
  validation {
    condition     = contains(["headless", "gui"], var.kali_deployment_mode)
    error_message = "kali_deployment_mode must be 'headless' or 'gui'."
  }
}

variable "kali_vm_size" {
  description = "Azure VM size for Kali. Leave empty to auto-pick."
  type        = string
  default     = ""
}

variable "kali_volume_size" {
  description = "Root volume size in GB for Kali. Leave 0 to auto-pick."
  type        = number
  default     = 0
}

variable "vnet_cidr" {
  description = "CIDR block for the TeamServer VNet"
  type        = string
  default     = "10.50.0.0/16"
}

variable "redirector_vnet_cidr" {
  description = "CIDR block for the Redirector VNet"
  type        = string
  default     = "10.60.0.0/16"
}

variable "enable_mythic_autostart" {
  description = "Automatically start Mythic on instance boot"
  type        = bool
  default     = true
}

variable "redirector_domain" {
  description = "Domain name for redirector (optional)"
  type        = string
  default     = ""
}

variable "enable_redirector_htaccess_filtering" {
  description = "Enable Apache mod_rewrite filtering on redirector"
  type        = bool
  default     = true
}

variable "mythic_uri_prefix" {
  description = "URI prefix for Mythic C2 callbacks"
  type        = string
  default     = "/cdn/media/stream"
}

variable "sliver_uri_prefix" {
  description = "URI prefix for Sliver C2 callbacks"
  type        = string
  default     = "/cloud/storage/objects"
}

variable "havoc_uri_prefix" {
  description = "URI prefix for Havoc C2 callbacks"
  type        = string
  default     = "/edge/cache/assets"
}

variable "c2_header_name" {
  description = "HTTP header name required for C2 traffic"
  type        = string
  default     = "X-Request-ID"
}

variable "c2_header_value" {
  description = "HTTP header value required for C2 traffic (leave empty to auto-generate)"
  type        = string
  default     = ""
}

variable "tags" {
  description = "Additional tags for all resources"
  type        = map(string)
  default     = {}
}
