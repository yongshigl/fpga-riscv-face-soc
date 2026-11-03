param(
    [Parameter(Mandatory = $true)]
    [string]$PdsLicense,

    [string]$OemLicense
)

$ErrorActionPreference = 'Stop'

$pdsRoot = 'D:\Pango\PDS_2022.2-SP6.4'
$pdsShell = Join-Path $pdsRoot 'bin\pds_shell.exe'

if (-not (Test-Path -LiteralPath $pdsShell -PathType Leaf)) {
    throw "PDS is not installed at $pdsRoot"
}

$pdsLicensePath = (Resolve-Path -LiteralPath $PdsLicense).Path
[Environment]::SetEnvironmentVariable('PANGO_LICENSE_FILE', $pdsLicensePath, 'User')
$env:PANGO_LICENSE_FILE = $pdsLicensePath

if ($OemLicense) {
    $oemLicensePath = (Resolve-Path -LiteralPath $OemLicense).Path
    [Environment]::SetEnvironmentVariable('SNPSLMD_LICENSE_FILE', $oemLicensePath, 'User')
    $env:SNPSLMD_LICENSE_FILE = $oemLicensePath
}

Write-Host "PANGO_LICENSE_FILE=$pdsLicensePath"
if ($OemLicense) {
    Write-Host "SNPSLMD_LICENSE_FILE=$oemLicensePath"
}

& $pdsShell -version
if ($LASTEXITCODE -ne 0) {
    throw "pds_shell version check failed with exit code $LASTEXITCODE"
}

Write-Host 'PDS license environment configured for the current user.'

