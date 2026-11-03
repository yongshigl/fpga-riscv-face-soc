$ErrorActionPreference = 'Stop'
$service = Get-Service -Name 'vmware-usbarbitrator64' -ErrorAction SilentlyContinue
if ($service -and $service.Status -eq 'Running') {
    Stop-Service -Name 'vmware-usbarbitrator64' -Force
    Start-Sleep -Seconds 2
}
pnputil /scan-devices | Out-Host
Start-Sleep -Seconds 3
Get-PnpDevice -PresentOnly |
    Where-Object { $_.InstanceId -match 'VID_0403|VID_10C4' } |
    Select-Object Status, Class, FriendlyName, InstanceId |
    Format-Table -AutoSize -Wrap | Out-Host
