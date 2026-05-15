# security_groups.tf - Network Security Group definitions for Azure

# ============================================================================
# MYTHIC NSG
# ============================================================================

resource "azurerm_network_security_group" "mythic" {
  name                = "${var.project_name}-mythic-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-mythic-nsg"
    VNet = "TeamServer-VNet"
  }
}

resource "azurerm_network_security_rule" "mythic_ssh" {
  name                        = "SSH-from-instructor"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.your_public_ip]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.mythic.name
}

resource "azurerm_network_security_rule" "mythic_ssh_guac" {
  name                        = "SSH-from-guacamole"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.mythic.name
}

resource "azurerm_network_security_rule" "mythic_web_ui" {
  name                        = "Mythic-UI-from-windows"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_ranges     = ["7443", "7444"]
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.mythic.name
}

resource "azurerm_network_security_rule" "mythic_c2_from_redirector" {
  name                        = "C2-from-redirector-vnet"
  priority                    = 130
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_ranges     = ["80", "443"]
  source_address_prefixes     = [var.redirector_vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.mythic.name
}

resource "azurerm_network_security_rule" "mythic_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.mythic.name
}

resource "azurerm_network_interface_security_group_association" "mythic" {
  network_interface_id      = azurerm_network_interface.mythic.id
  network_security_group_id = azurerm_network_security_group.mythic.id
}

# ============================================================================
# GUACAMOLE NSG
# ============================================================================

resource "azurerm_network_security_group" "guacamole" {
  name                = "${var.project_name}-guacamole-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-guacamole-nsg"
    VNet = "TeamServer-VNet"
  }
}

resource "azurerm_network_security_rule" "guacamole_ssh" {
  name                        = "SSH-from-instructor"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.your_public_ip]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.guacamole.name
}

resource "azurerm_network_security_rule" "guacamole_https" {
  name                        = "HTTPS-from-anywhere"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefixes     = ["*"]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.guacamole.name
}

resource "azurerm_network_security_rule" "guacamole_http" {
  name                        = "HTTP-redirect"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "80"
  source_address_prefixes     = ["*"]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.guacamole.name
}

resource "azurerm_network_security_rule" "guacamole_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.guacamole.name
}

resource "azurerm_network_interface_security_group_association" "guacamole" {
  network_interface_id      = azurerm_network_interface.guacamole.id
  network_security_group_id = azurerm_network_security_group.guacamole.id
}

# ============================================================================
# WINDOWS NSG
# ============================================================================

resource "azurerm_network_security_group" "windows" {
  name                = "${var.project_name}-windows-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-windows-nsg"
    VNet = "TeamServer-VNet"
  }
}

resource "azurerm_network_security_rule" "windows_rdp_guac" {
  name                        = "RDP-from-guacamole"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3389"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.windows.name
}

resource "azurerm_network_security_rule" "windows_rdp_instructor" {
  name                        = "RDP-from-instructor-temp"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3389"
  source_address_prefixes     = [var.your_public_ip]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.windows.name
}

resource "azurerm_network_security_rule" "windows_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.windows.name
}

resource "azurerm_network_interface_security_group_association" "windows" {
  network_interface_id      = azurerm_network_interface.windows.id
  network_security_group_id = azurerm_network_security_group.windows.id
}

# ============================================================================
# SLIVER NSG
# ============================================================================

resource "azurerm_network_security_group" "sliver" {
  name                = "${var.project_name}-sliver-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-sliver-nsg"
    VNet = "TeamServer-VNet"
  }
}

resource "azurerm_network_security_rule" "sliver_ssh" {
  name                        = "SSH-from-instructor"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.your_public_ip]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.sliver.name
}

resource "azurerm_network_security_rule" "sliver_ssh_guac" {
  name                        = "SSH-from-guacamole"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.sliver.name
}

resource "azurerm_network_security_rule" "sliver_c2" {
  name                        = "C2-from-redirector-vnet"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_ranges     = ["80", "443"]
  source_address_prefixes     = [var.redirector_vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.sliver.name
}

resource "azurerm_network_security_rule" "sliver_mux" {
  name                        = "Sliver-multiplexer"
  priority                    = 130
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "31337"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.sliver.name
}

resource "azurerm_network_security_rule" "sliver_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.sliver.name
}

resource "azurerm_network_interface_security_group_association" "sliver" {
  network_interface_id      = azurerm_network_interface.sliver.id
  network_security_group_id = azurerm_network_security_group.sliver.id
}

# ============================================================================
# HAVOC NSG
# ============================================================================

resource "azurerm_network_security_group" "havoc" {
  name                = "${var.project_name}-havoc-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-havoc-nsg"
    VNet = "TeamServer-VNet"
  }
}

resource "azurerm_network_security_rule" "havoc_ssh" {
  name                        = "SSH-from-instructor"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.your_public_ip]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.havoc.name
}

resource "azurerm_network_security_rule" "havoc_ssh_guac" {
  name                        = "SSH-from-guacamole"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.havoc.name
}

resource "azurerm_network_security_rule" "havoc_c2" {
  name                        = "C2-from-redirector-vnet"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_ranges     = ["80", "443"]
  source_address_prefixes     = [var.redirector_vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.havoc.name
}

resource "azurerm_network_security_rule" "havoc_teamserver" {
  name                        = "Havoc-teamserver"
  priority                    = 130
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "40056"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.havoc.name
}

resource "azurerm_network_security_rule" "havoc_vnc" {
  name                        = "Havoc-VNC-from-guacamole"
  priority                    = 140
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "5901"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.havoc.name
}

resource "azurerm_network_security_rule" "havoc_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.havoc.name
}

resource "azurerm_network_interface_security_group_association" "havoc" {
  network_interface_id      = azurerm_network_interface.havoc.id
  network_security_group_id = azurerm_network_security_group.havoc.id
}

# ============================================================================
# REDIRECTOR NSG
# ============================================================================

resource "azurerm_network_security_group" "redirector" {
  name                = "${var.project_name}-redirector-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-redirector-nsg"
    VNet = "Redirector-VNet"
    Note = "Simulates external VPS firewall rules"
  }
}

resource "azurerm_network_security_rule" "redirector_http" {
  name                        = "HTTP-from-anywhere"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "80"
  source_address_prefixes     = ["*"]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.redirector.name
}

resource "azurerm_network_security_rule" "redirector_https" {
  name                        = "HTTPS-from-anywhere"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "443"
  source_address_prefixes     = ["*"]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.redirector.name
}

resource "azurerm_network_security_rule" "redirector_internal" {
  name                        = "All-from-teamserver-vnet"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.redirector.name
}

resource "azurerm_network_security_rule" "redirector_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.redirector.name
}

resource "azurerm_network_interface_security_group_association" "redirector" {
  network_interface_id      = azurerm_network_interface.redirector.id
  network_security_group_id = azurerm_network_security_group.redirector.id
}

# ============================================================================
# KALI NSG
# ============================================================================

resource "azurerm_network_security_group" "kali" {
  name                = "${var.project_name}-kali-nsg"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name

  tags = {
    Name = "${var.project_name}-kali-nsg"
    VNet = "TeamServer-VNet"
  }
}

resource "azurerm_network_security_rule" "kali_ssh" {
  name                        = "SSH-from-instructor"
  priority                    = 100
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.your_public_ip]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.kali.name
}

resource "azurerm_network_security_rule" "kali_ssh_guac" {
  name                        = "SSH-from-guacamole"
  priority                    = 110
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "22"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.kali.name
}

resource "azurerm_network_security_rule" "kali_xrdp" {
  name                        = "XRDP-from-guacamole"
  priority                    = 120
  direction                   = "Inbound"
  access                      = "Allow"
  protocol                    = "Tcp"
  source_port_range           = "*"
  destination_port_range      = "3389"
  source_address_prefixes     = [var.vnet_cidr]
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.kali.name
}

resource "azurerm_network_security_rule" "kali_egress" {
  name                        = "Allow-All-Outbound"
  priority                    = 100
  direction                   = "Outbound"
  access                      = "Allow"
  protocol                    = "*"
  source_port_range           = "*"
  destination_port_range      = "*"
  source_address_prefix       = "*"
  destination_address_prefix  = "*"
  resource_group_name         = azurerm_resource_group.main.name
  network_security_group_name = azurerm_network_security_group.kali.name
}

resource "azurerm_network_interface_security_group_association" "kali" {
  network_interface_id      = azurerm_network_interface.kali.id
  network_security_group_id = azurerm_network_security_group.kali.id
}
