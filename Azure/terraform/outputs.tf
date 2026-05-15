# outputs.tf - Output values after Azure deployment

locals {
  deployment_info_content = <<-EOT

  +---------------------------------------------------------------------+
  | 1. GUACAMOLE PORTAL                                                 |
  +---------------------------------------------------------------------+
    URL:          https://${azurerm_public_ip.guacamole.ip_address}/guacamole
    Public IP:    ${azurerm_public_ip.guacamole.ip_address}
    Private IP:   ${azurerm_network_interface.guacamole.private_ip_address}
    UI Username:  guacadmin
    UI Password:  ${nonsensitive(random_password.lab.result)}
    SSH Username: admin
    SSH Password: ${nonsensitive(random_password.lab.result)}
    SSH:          ssh admin@${azurerm_public_ip.guacamole.ip_address}

  +---------------------------------------------------------------------+
  | 2. MYTHIC C2                                                        |
  +---------------------------------------------------------------------+
    Web UI:       https://mythic:7443  (open from Windows via Guacamole)
    Private IP:   ${azurerm_network_interface.mythic.private_ip_address}
    SSH Username: admin
    SSH Password: ${nonsensitive(random_password.lab.result)}
    UI Username:  mythic_admin
    UI Password:  ${nonsensitive(random_password.lab.result)}

  +---------------------------------------------------------------------+
  | 3. SLIVER C2                                                        |
  +---------------------------------------------------------------------+
    Private IP:   ${azurerm_network_interface.sliver.private_ip_address}
    SSH Username: admin
    SSH Password: ${nonsensitive(random_password.lab.result)}
    Sliver Op:    admin (config at /home/admin/.sliver-client/configs/admin.cfg)
    Multiplexer:  Port 31337

  +---------------------------------------------------------------------+
  | 4. HAVOC C2                                                         |
  +---------------------------------------------------------------------+
    Private IP:   ${azurerm_network_interface.havoc.private_ip_address}
    SSH Username: admin
    SSH Password: ${nonsensitive(random_password.lab.result)}
    Havoc User:   admin
    Havoc Pass:   ${nonsensitive(random_password.lab.result)}
    Teamserver:   Host: havoc  |  Port: 40056  |  User: admin
    Guacamole:    Havoc Desktop (VNC) | Havoc (SSH)

  +---------------------------------------------------------------------+
  | 5. REDIRECTOR                                                       |
  +---------------------------------------------------------------------+
    Public IP:    ${azurerm_public_ip.redirector.ip_address}
    Private IP:   ${azurerm_network_interface.redirector.private_ip_address}
    Domain:       ${var.redirector_domain != "" ? var.redirector_domain : "c2.example.com"}
    SSH Username: admin
    SSH Password: ${nonsensitive(random_password.lab.result)}
    Note:         Public SSH on port 22 is NOT exposed; only 80/443 are reachable
    C2 Header:    ${var.c2_header_name}: ${local.c2_header_value}
    URI Routing:  ${var.mythic_uri_prefix}/ -> Mythic
                  ${var.sliver_uri_prefix}/ -> Sliver
                  ${var.havoc_uri_prefix}/ -> Havoc
    Decoy Page:   CloudEdge CDN maintenance (no header = decoy)

  +---------------------------------------------------------------------+
  | 6. WINDOWS                                                          |
  +---------------------------------------------------------------------+
    Private IP:   ${azurerm_network_interface.windows.private_ip_address}
    Username:     Administrator
    Password:     ${var.admin_password}
    Access:       RDP via Guacamole

  +---------------------------------------------------------------------+
  | 7. KALI                                                             |
  +---------------------------------------------------------------------+
    Private IP:   ${azurerm_network_interface.kali.private_ip_address}
    Mode:         ${upper(var.kali_deployment_mode)}
    SSH Username: admin
    SSH Password: ${nonsensitive(random_password.lab.result)}
    Guacamole:    Kali (SSH)${var.kali_deployment_mode == "gui" ? " | Kali (XRDP)" : ""}

  EOT

  network_architecture_content = <<-EOT

  +---------------------------------------------------------------------+
  |                     NETWORK ARCHITECTURE                            |
  +---------------------------------------------------------------------+

  VNet A: Teamserver (${var.vnet_cidr})
  +-------------------------+-------------------------------------------+
  |  mythic                 |  ${azurerm_network_interface.mythic.private_ip_address}
  |  sliver                 |  ${azurerm_network_interface.sliver.private_ip_address}
  |  havoc                  |  ${azurerm_network_interface.havoc.private_ip_address}
  |  guacamole              |  ${azurerm_network_interface.guacamole.private_ip_address} (priv)  /  ${azurerm_public_ip.guacamole.ip_address} (pub)
  |  windows                |  ${azurerm_network_interface.windows.private_ip_address}
  |  kali                   |  ${azurerm_network_interface.kali.private_ip_address} (${var.kali_deployment_mode})
  +-------------------------+-------------------------------------------+

  VNet B: Redirector (${var.redirector_vnet_cidr})
  +-------------------------+-------------------------------------------+
  |  redirector             |  ${azurerm_network_interface.redirector.private_ip_address} (priv)  /  ${azurerm_public_ip.redirector.ip_address} (pub)
  +-------------------------+-------------------------------------------+

  VNet Peering: VNet A <-> VNet B

  C2 Traffic Flow  (ports 80/443, header + URI validation):

    [Target]
       |
       v  HTTPS / HTTP
    ${azurerm_public_ip.redirector.ip_address}  (redirector)
       |
       |  Required:  ${var.c2_header_name}: ${local.c2_header_value}
       |
       +--  ${format("%-25s", format("%s/", var.mythic_uri_prefix))}-->  ${format("%-15s", azurerm_network_interface.mythic.private_ip_address)}  (mythic)
       +--  ${format("%-25s", format("%s/", var.sliver_uri_prefix))}-->  ${format("%-15s", azurerm_network_interface.sliver.private_ip_address)}  (sliver)
       +--  ${format("%-25s", format("%s/", var.havoc_uri_prefix))}-->  ${format("%-15s", azurerm_network_interface.havoc.private_ip_address)}  (havoc)
       +--  ${format("%-25s", "[no valid header]")}-->  Decoy page (CloudEdge CDN)
  EOT
}

output "deployment_info" {
  description = "Full deployment details for all lab instances"
  value       = local.deployment_info_content
}

output "network_architecture" {
  description = "Network architecture diagram with actual IPs"
  value       = local.network_architecture_content
}

resource "local_file" "deployment_info" {
  filename = "${path.root}/../deployment_info.txt"
  content  = "${local.deployment_info_content}\n${local.network_architecture_content}"
}
