$ErrorActionPreference = 'Stop'
$device = 'USB\VID_10C4&PID_EA60\0001'
$parameters = 'HKLM:\SYSTEM\CurrentControlSet\Enum\USB\VID_10C4&PID_EA60\0001\Device Parameters'
Set-ItemProperty -Path $parameters -Name PortName -Value 'COM7'
pnputil /restart-device "$device" | Out-Host
if ($LASTEXITCODE -ne 0) { throw "pnputil failed: $LASTEXITCODE" }
