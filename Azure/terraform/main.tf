# main.tf - Azure Resource Definitions for redStack Lab

terraform {
  required_version = ">= 1.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
    local = {
      source  = "hashicorp/local"
      version = "~> 2.0"
    }
  }
}

provider "azurerm" {
  features {
    virtual_machine {
      delete_os_disk_on_deletion     = true
      skip_provider_registration     = false
    }
  }

  resource_provider_registrations = "core"
}

# Random password for all lab instances
resource "random_password" "lab" {
  length           = 16
  special          = true
  min_special      = 2
  override_special = "-_.~!@"
}

# Random token for C2 header validation
resource "random_id" "c2_header_token" {
  byte_length = 16
}

# ============================================================================
# RESOURCE GROUP
# ============================================================================

resource "azurerm_resource_group" "main" {
  name     = var.resource_group_name
  location = var.location

  tags = merge({
    Project     = var.project_name
    ManagedBy   = "Terraform"
    Environment = "Training"
  }, var.tags)
}

# ============================================================================
# VNET A: Team Server VPC Equivalent
# ============================================================================

resource "azurerm_virtual_network" "teamserver" {
  name                = "${var.project_name}-TeamServer-VNet"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = [var.vnet_cidr]

  tags = {
    Role = "Team Server Infrastructure"
  }
}

resource "azurerm_subnet" "teamserver" {
  name                 = "${var.project_name}-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.teamserver.name
  address_prefixes     = [cidrsubnet(var.vnet_cidr, 8, 1)]
}

# ============================================================================
# VNET B: Redirector VPC Equivalent (simulates external provider)
# ============================================================================

resource "azurerm_virtual_network" "redirector" {
  name                = "${var.project_name}-Redirector-VNet"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  address_space       = [var.redirector_vnet_cidr]

  tags = {
    Role = "Redirector Infrastructure"
    Note = "Simulates external VPS provider network"
  }
}

resource "azurerm_subnet" "redirector" {
  name                 = "${var.project_name}-redirector-subnet"
  resource_group_name  = azurerm_resource_group.main.name
  virtual_network_name = azurerm_virtual_network.redirector.name
  address_prefixes     = [cidrsubnet(var.redirector_vnet_cidr, 8, 1)]
}

# ============================================================================
# VNET PEERING
# ============================================================================

resource "azurerm_virtual_network_peering" "teamserver_to_redirector" {
  name                      = "${var.project_name}-ts-to-redirector"
  resource_group_name       = azurerm_resource_group.main.name
  virtual_network_name      = azurerm_virtual_network.teamserver.name
  remote_virtual_network_id = azurerm_virtual_network.redirector.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

resource "azurerm_virtual_network_peering" "redirector_to_teamserver" {
  name                      = "${var.project_name}-redirector-to-ts"
  resource_group_name       = azurerm_resource_group.main.name
  virtual_network_name      = azurerm_virtual_network.redirector.name
  remote_virtual_network_id = azurerm_virtual_network.teamserver.id
  allow_virtual_network_access = true
  allow_forwarded_traffic      = true
}

# ============================================================================
# PUBLIC IPs
# ============================================================================

resource "azurerm_public_ip" "guacamole" {
  name                = "${var.project_name}-guacamole-pip"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"

  tags = { Name = "${var.project_name}-guacamole-pip" }
}

resource "azurerm_public_ip" "redirector" {
  name                = "${var.project_name}-redirector-pip"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  allocation_method   = "Static"
  sku                 = "Standard"

  tags = { Name = "${var.project_name}-redirector-pip" }
}

# ============================================================================
# LOCALS
# ============================================================================

locals {
  c2_header_value = var.c2_header_value != "" ? var.c2_header_value : random_id.c2_header_token.hex
}

# ============================================================================
# NETWORK INTERFACES
# ============================================================================

resource "azurerm_network_interface" "mythic" {
  name                = "${var.project_name}-mythic-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.teamserver.id
    private_ip_address_allocation = "Dynamic"
  }

  tags = { Name = "${var.project_name}-mythic-nic" }
}

resource "azurerm_network_interface" "guacamole" {
  name                = "${var.project_name}-guacamole-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.teamserver.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.guacamole.id
  }

  tags = { Name = "${var.project_name}-guacamole-nic" }
}

resource "azurerm_network_interface" "windows" {
  name                = "${var.project_name}-windows-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.teamserver.id
    private_ip_address_allocation = "Dynamic"
  }

  tags = { Name = "${var.project_name}-windows-nic" }
}

resource "azurerm_network_interface" "kali" {
  name                = "${var.project_name}-kali-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.teamserver.id
    private_ip_address_allocation = "Dynamic"
  }

  tags = { Name = "${var.project_name}-kali-nic" }
}

resource "azurerm_network_interface" "sliver" {
  name                = "${var.project_name}-sliver-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.teamserver.id
    private_ip_address_allocation = "Dynamic"
  }

  tags = { Name = "${var.project_name}-sliver-nic" }
}

resource "azurerm_network_interface" "havoc" {
  name                = "${var.project_name}-havoc-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.teamserver.id
    private_ip_address_allocation = "Dynamic"
  }

  tags = { Name = "${var.project_name}-havoc-nic" }
}

resource "azurerm_network_interface" "redirector" {
  name                = "${var.project_name}-redirector-nic"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  ip_configuration {
    name                          = "internal"
    subnet_id                     = azurerm_subnet.redirector.id
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = azurerm_public_ip.redirector.id
  }

  tags = { Name = "${var.project_name}-redirector-nic" }
}

# ============================================================================
# SSH KEY
# ============================================================================

resource "azurerm_ssh_public_key" "lab" {
  name                = "${var.project_name}-ssh-key"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  public_key          = file(var.ssh_public_key_path)
}

# ============================================================================
# MYTHIC TEAM SERVER
# ============================================================================

resource "azurerm_linux_virtual_machine" "mythic" {
  name                  = "${var.project_name}-mythic"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.mythic_vm_size
  admin_username        = "admin"
  admin_password        = var.admin_password
  disable_password_authentication = false
  network_interface_ids = [azurerm_network_interface.mythic.id]

  admin_ssh_key {
    username   = "admin"
    public_key = azurerm_ssh_public_key.lab.public_key
  }

  os_disk {
    name                 = "${var.project_name}-mythic-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "debian"
    offer     = "debian-12"
    sku       = "12-gen2"
    version   = "latest"
  }

  custom_data = base64encode(templatefile("${path.module}/setup_scripts/mythic_setup.sh", {
    localPub_ip            = var.your_public_ip
    enable_autostart       = var.enable_mythic_autostart
    ssh_password           = random_password.lab.result
    vpc_cidr               = var.vnet_cidr
    redirector_vpc_cidr    = var.redirector_vnet_cidr
    mythic_private_ip      = azurerm_network_interface.mythic.private_ip_address
    guacamole_private_ip   = azurerm_network_interface.guacamole.private_ip_address
    sliver_private_ip      = azurerm_network_interface.sliver.private_ip_address
    havoc_private_ip       = azurerm_network_interface.havoc.private_ip_address
    redirector_private_ip  = azurerm_network_interface.redirector.private_ip_address
    windows_private_ip     = azurerm_network_interface.windows.private_ip_address
    kali_private_ip        = azurerm_network_interface.kali.private_ip_address
    mythic_admin_password  = random_password.lab.result
  }))

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Name     = "${var.project_name}-mythic"
    Role     = "c2"
    Hostname = "mythic"
  }
}

# ============================================================================
# GUACAMOLE SERVER
# ============================================================================

data "azurerm_public_ip" "guacamole" {
  name                = azurerm_public_ip.guacamole.name
  resource_group_name = azurerm_resource_group.main.name
  depends_on          = [azurerm_linux_virtual_machine.guacamole]
}

resource "azurerm_linux_virtual_machine" "guacamole" {
  name                  = "${var.project_name}-guacamole"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.guacamole_vm_size
  admin_username        = "admin"
  admin_password        = var.admin_password
  disable_password_authentication = false
  network_interface_ids = [azurerm_network_interface.guacamole.id]

  admin_ssh_key {
    username   = "admin"
    public_key = azurerm_ssh_public_key.lab.public_key
  }

  os_disk {
    name                 = "${var.project_name}-guacamole-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 20
  }

  source_image_reference {
    publisher = "debian"
    offer     = "debian-12"
    sku       = "12-gen2"
    version   = "latest"
  }

  custom_data = base64encode(templatefile("${path.module}/setup_scripts/guacamole_userdata.sh", {
    ssh_password          = random_password.lab.result
    guacamole_private_ip  = azurerm_network_interface.guacamole.private_ip_address
    mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
    sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
    havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
    redirector_private_ip = azurerm_network_interface.redirector.private_ip_address
    windows_private_ip    = azurerm_network_interface.windows.private_ip_address
    kali_private_ip       = azurerm_network_interface.kali.private_ip_address
    guac_public_ip        = azurerm_public_ip.guacamole.ip_address
    setup_script_b64 = base64gzip(replace(templatefile("${path.module}/setup_scripts/guacamole_setup.sh", {
      guac_admin_password   = random_password.lab.result
      windows_private_ip    = azurerm_network_interface.windows.private_ip_address
      windows_username      = "Administrator"
      windows_password      = var.admin_password
      ssh_password          = random_password.lab.result
      mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
      redirector_private_ip = azurerm_network_interface.redirector.private_ip_address
      sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
      havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
      guacamole_private_ip  = azurerm_network_interface.guacamole.private_ip_address
      kali_private_ip       = azurerm_network_interface.kali.private_ip_address
      kali_deployment_mode  = var.kali_deployment_mode
      enable_vpn_tunnel     = false
      vpn_tunnel_cidrs      = []
      guac_public_ip        = azurerm_public_ip.guacamole.ip_address
    }), "\r", ""))
  }), "\r", "")

  depends_on = [azurerm_windows_virtual_machine.windows]

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Name     = "${var.project_name}-guacamole"
    Role     = "portal"
    Hostname = "guac"
  }
}

# ============================================================================
# WINDOWS SRV2022 CLIENT
# ============================================================================

resource "azurerm_windows_virtual_machine" "windows" {
  name                = "${var.project_name}-windows"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  size                = var.windows_vm_size
  admin_username      = "Administrator"
  admin_password      = var.admin_password
  network_interface_ids = [azurerm_network_interface.windows.id]

  os_disk {
    name                 = "${var.project_name}-windows-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 50
  }

  source_image_reference {
    publisher = "MicrosoftWindowsServer"
    offer     = "WindowsServer"
    sku       = "2022-datacenter-azure-edition"
    version   = "latest"
  }

  custom_data = base64encode(replace(
    file("${path.module}/setup_scripts/windows_setup.ps1"),
    "__HOSTS_ENTRIES__",
    join("\r\n", [
      "# redStack lab hosts",
      "${azurerm_network_interface.guacamole.private_ip_address}    guac",
      "${azurerm_network_interface.mythic.private_ip_address}    mythic",
      "${azurerm_network_interface.sliver.private_ip_address}    sliver",
      "${azurerm_network_interface.havoc.private_ip_address}    havoc",
      "${azurerm_network_interface.redirector.private_ip_address}    redirector",
      "${azurerm_network_interface.kali.private_ip_address}    kali",
    ])
  ))

  tags = {
    Name     = "${var.project_name}-windows"
    Role     = "workstation"
    Hostname = "windows"
  }
}

# ============================================================================
# SLIVER C2 SERVER
# ============================================================================

resource "azurerm_linux_virtual_machine" "sliver" {
  name                  = "${var.project_name}-sliver"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.sliver_vm_size
  admin_username        = "admin"
  admin_password        = var.admin_password
  disable_password_authentication = false
  network_interface_ids = [azurerm_network_interface.sliver.id]

  admin_ssh_key {
    username   = "admin"
    public_key = azurerm_ssh_public_key.lab.public_key
  }

  os_disk {
    name                 = "${var.project_name}-sliver-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 25
  }

  source_image_reference {
    publisher = "debian"
    offer     = "debian-12"
    sku       = "12-gen2"
    version   = "latest"
  }

  custom_data = base64encode(templatefile("${path.module}/setup_scripts/sliver_setup.sh", {
    ssh_password          = random_password.lab.result
    redirector_vpc_cidr   = var.redirector_vnet_cidr
    sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
    guacamole_private_ip  = azurerm_network_interface.guacamole.private_ip_address
    mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
    havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
    redirector_private_ip = azurerm_network_interface.redirector.private_ip_address
    windows_private_ip    = azurerm_network_interface.windows.private_ip_address
    kali_private_ip       = azurerm_network_interface.kali.private_ip_address
    c2_header_name        = var.c2_header_name
    c2_header_value       = local.c2_header_value
  }))

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Name     = "${var.project_name}-sliver"
    Role     = "c2"
    Hostname = "sliver"
  }
}

# ============================================================================
# HAVOC C2 SERVER
# ============================================================================

resource "azurerm_linux_virtual_machine" "havoc" {
  name                  = "${var.project_name}-havoc"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.havoc_vm_size
  admin_username        = "admin"
  admin_password        = var.admin_password
  disable_password_authentication = false
  network_interface_ids = [azurerm_network_interface.havoc.id]

  admin_ssh_key {
    username   = "admin"
    public_key = azurerm_ssh_public_key.lab.public_key
  }

  os_disk {
    name                 = "${var.project_name}-havoc-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 30
  }

  source_image_reference {
    publisher = "debian"
    offer     = "debian-12"
    sku       = "12-gen2"
    version   = "latest"
  }

  custom_data = base64encode(templatefile("${path.module}/setup_scripts/havoc_setup.sh", {
    ssh_password          = random_password.lab.result
    main_vpc_cidr         = var.vnet_cidr
    redirector_vpc_cidr   = var.redirector_vnet_cidr
    havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
    guacamole_private_ip  = azurerm_network_interface.guacamole.private_ip_address
    mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
    sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
    redirector_private_ip = azurerm_network_interface.redirector.private_ip_address
    windows_private_ip    = azurerm_network_interface.windows.private_ip_address
    kali_private_ip       = azurerm_network_interface.kali.private_ip_address
  }))

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Name     = "${var.project_name}-havoc"
    Role     = "c2"
    Hostname = "havoc"
  }
}

# ============================================================================
# REDIRECTOR (Apache reverse proxy)
# ============================================================================

data "azurerm_public_ip" "redirector" {
  name                = azurerm_public_ip.redirector.name
  resource_group_name = azurerm_resource_group.main.name
  depends_on          = [azurerm_linux_virtual_machine.redirector]
}

resource "azurerm_linux_virtual_machine" "redirector" {
  name                  = "${var.project_name}-redirector"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = var.redirector_vm_size
  admin_username        = "admin"
  admin_password        = var.admin_password
  disable_password_authentication = false
  network_interface_ids = [azurerm_network_interface.redirector.id]

  admin_ssh_key {
    username   = "admin"
    public_key = azurerm_ssh_public_key.lab.public_key
  }

  os_disk {
    name                 = "${var.project_name}-redirector-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = 20
  }

  source_image_reference {
    publisher = "debian"
    offer     = "debian-12"
    sku       = "12-gen2"
    version   = "latest"
  }

  custom_data = base64encode(templatefile("${path.module}/setup_scripts/redirector_userdata.sh", {
    ssh_password          = random_password.lab.result
    redirector_private_ip = azurerm_network_interface.redirector.private_ip_address
    guacamole_private_ip  = azurerm_network_interface.guacamole.private_ip_address
    mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
    sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
    havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
    windows_private_ip    = azurerm_network_interface.windows.private_ip_address
    kali_private_ip       = azurerm_network_interface.kali.private_ip_address
    redirector_public_ip  = azurerm_public_ip.redirector.ip_address
    setup_script_b64 = base64gzip(replace(templatefile("${path.module}/setup_scripts/redirector_setup.sh", {
      mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
      sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
      havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
      domain_name           = var.redirector_domain
      mythic_uri_prefix     = var.mythic_uri_prefix
      sliver_uri_prefix     = var.sliver_uri_prefix
      havoc_uri_prefix      = var.havoc_uri_prefix
      c2_header_name        = var.c2_header_name
      c2_header_value       = local.c2_header_value
      enable_vpn_tunnel     = false
      enable_redirect_rules = var.enable_redirector_htaccess_filtering
      main_vpc_cidr         = var.vnet_cidr
      redirector_public_ip  = azurerm_public_ip.redirector.ip_address
    }), "\r", ""))
  }), "\r", "")

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Name     = "${var.project_name}-redirector"
    Role     = "redirector"
    Hostname = "redirector"
    Note     = "Simulates external VPS (separate VNet)"
  }
}

# ============================================================================
# KALI OPERATOR WORKSTATION
# ============================================================================

locals {
  kali_vm_size   = var.kali_vm_size != "" ? var.kali_vm_size : "Standard_B2s"
  kali_disk_size = var.kali_volume_size != 0 ? var.kali_volume_size : (var.kali_deployment_mode == "gui" ? 50 : 30)
}

resource "azurerm_linux_virtual_machine" "kali" {
  name                  = "${var.project_name}-kali"
  location              = azurerm_resource_group.main.location
  resource_group_name   = azurerm_resource_group.main.name
  size                  = local.kali_vm_size
  admin_username        = "admin"
  admin_password        = var.admin_password
  disable_password_authentication = false
  network_interface_ids = [azurerm_network_interface.kali.id]

  admin_ssh_key {
    username   = "admin"
    public_key = azurerm_ssh_public_key.lab.public_key
  }

  os_disk {
    name                 = "${var.project_name}-kali-osdisk"
    caching              = "ReadWrite"
    storage_account_type = "StandardSSD_LRS"
    disk_size_gb         = local.kali_disk_size
  }

  source_image_reference {
    publisher = "kali-linux"
    offer     = "kali"
    sku       = "kali-2024-4"
    version   = "latest"
  }

  plan {
    name      = "kali-2024-4"
    publisher = "kali-linux"
    product   = "kali"
  }

  custom_data = base64encode(templatefile("${path.module}/setup_scripts/kali_setup.sh", {
    ssh_password          = random_password.lab.result
    kali_deployment_mode  = var.kali_deployment_mode
    redirector_vpc_cidr   = var.redirector_vnet_cidr
    kali_private_ip       = azurerm_network_interface.kali.private_ip_address
    guacamole_private_ip  = azurerm_network_interface.guacamole.private_ip_address
    mythic_private_ip     = azurerm_network_interface.mythic.private_ip_address
    sliver_private_ip     = azurerm_network_interface.sliver.private_ip_address
    havoc_private_ip      = azurerm_network_interface.havoc.private_ip_address
    redirector_private_ip = azurerm_network_interface.redirector.private_ip_address
    windows_private_ip    = azurerm_network_interface.windows.private_ip_address
  }))

  identity {
    type = "SystemAssigned"
  }

  tags = {
    Name     = "${var.project_name}-kali"
    Role     = "workstation"
    Hostname = "kali"
    Mode     = var.kali_deployment_mode
  }
}
