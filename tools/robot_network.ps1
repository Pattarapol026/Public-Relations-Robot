# Puts the laptop's wired port on the robot's network (192.168.10.x), or takes it back off.
#
#   Right-click the file -> "Run with PowerShell", or double-click tools\robot_network.bat.
#   It asks for administrator rights (the Windows prompt) because it changes a network adapter.
#
# What it does (nothing else):
#   * "Ethernet": no DHCP, address 192.168.10.4/24 (the robot is 192.168.10.1), interface metric 5 so that traffic for
#     192.168.10.x goes out of the cable and not through VirtualBox's "Ethernet 2", which also sits on 192.168.10.x
#   * leaves "Ethernet 2" (VirtualBox) alone
# Undo:  powershell -File tools\robot_network.ps1 -Undo   (back to DHCP)
param([switch]$Undo, [string]$Adapter = "Ethernet", [string]$Address = "192.168.10.4")

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal $identity).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $arguments = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$PSCommandPath`"", "-Adapter", $Adapter, "-Address", $Address)
    if ($Undo) { $arguments += "-Undo" }
    Start-Process powershell -Verb RunAs -ArgumentList $arguments
    exit
}

try {
    if ($Undo) {
        Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.PrefixOrigin -eq "Manual" } | Remove-NetIPAddress -Confirm:$false
        Set-NetIPInterface -InterfaceAlias $Adapter -Dhcp Enabled -AutomaticMetric Enabled
        Write-Host "'$Adapter' is back on DHCP."
    } else {
        Set-NetIPInterface -InterfaceAlias $Adapter -Dhcp Disabled -InterfaceMetric 5
        Get-NetIPAddress -InterfaceAlias $Adapter -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Remove-NetIPAddress -Confirm:$false
        New-NetIPAddress -InterfaceAlias $Adapter -IPAddress $Address -PrefixLength 24 | Out-Null
        Start-Sleep -Seconds 3
        $reached = Test-Connection 192.168.10.1 -Count 2 -Quiet
        Write-Host "'$Adapter' is $Address/24."
        Write-Host ("Robot 192.168.10.1: " + $(if ($reached) { "answers" } else { "does not answer (is the cable in the robot, and the robot on?)" }))
    }
} catch {
    Write-Host "Failed: $($_.Exception.Message)" -ForegroundColor Red
}
Read-Host "Press Enter to close"
