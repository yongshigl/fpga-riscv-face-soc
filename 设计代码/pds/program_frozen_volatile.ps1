param(
    [Parameter(Mandatory = $true)][string]$ProjectDirectory,
    [Parameter(Mandatory = $true)][string]$EvidenceDirectory,
    [ValidateRange(1024, 65535)][int]$JtagPort = 65450
)
$ErrorActionPreference = 'Stop'
$project = [IO.Path]::GetFullPath($ProjectDirectory)
$output = [IO.Path]::GetFullPath($EvidenceDirectory)
if (Test-Path -LiteralPath $output) { throw 'Use a new JTAG evidence directory' }
$manifestPath = Join-Path $project 'manifest.json'
$manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
$bitstreams = @(Get-ChildItem -LiteralPath (Join-Path $project 'generate_bitstream') -Filter '*.sbit')
if ($bitstreams.Count -ne 1 -or -not $manifest.timing_met -or -not $manifest.volatile_only -or
    (Get-FileHash -LiteralPath $bitstreams[0].FullName).Hash -ne $manifest.bitstream_sha256) {
    throw 'A timing-closed frozen project and matching volatile bitstream are required'
}
foreach ($source in $manifest.sources) {
    $path = Join-Path $project $source.name
    if (-not (Test-Path -LiteralPath $path)) { $path = Join-Path (Join-Path $project 'src') $source.name }
    if ((Get-FileHash -LiteralPath $path).Hash -ne $source.sha256) {
        throw "Frozen source hash differs: $($source.name)"
    }
}
foreach ($constraint in $manifest.constraints) {
    if ((Get-FileHash -LiteralPath (Join-Path $project $constraint.name)).Hash -ne $constraint.sha256) {
        throw "Frozen constraint hash differs: $($constraint.name)"
    }
}
New-Item -ItemType Directory -Path $output | Out-Null
$bin = if ($env:PDS_BIN) { $env:PDS_BIN } else { 'C:\Pango\PDS_2022.2-SP6.4\bin' }
# PDS 2022.2 cannot open Tcl/work paths containing Chinese characters.
# Stage only the bitstream in an independent ASCII directory; keep requested
# evidence paths and the original frozen project intact.
$work = Join-Path ([IO.Path]::GetTempPath()) ([Guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Force -Path $work | Out-Null
$stagedBitstream = Join-Path $work 'frozen.sbit'
Copy-Item -LiteralPath $bitstreams[0].FullName -Destination $stagedBitstream
if ((Get-FileHash -LiteralPath $stagedBitstream).Hash -ne $manifest.bitstream_sha256) { throw 'Staged bitstream differs' }
$script = Join-Path $work 'program_volatile.tcl'
$bitstreamTcl = $stagedBitstream.Replace('\', '/')
@("cfg_connect -ip 127.0.0.1 -port $JtagPort", 'cfg_scan_chain',
  ('cfg_assign_file -file {' + $bitstreamTcl + '} -device_index 0'),
  'cfg_program -device_index 0', 'puts "FPGA_VOLATILE_PROGRAM_COMPLETE"', 'cfg_disconnect') |
    Set-Content -Encoding Ascii $script
$started, $server = [DateTime]::UtcNow, $null
try {
    $server = Start-Process -FilePath "$bin\cdt_js.exe" -ArgumentList @('-port', $JtagPort, '-work_dir', $work) `
        -WorkingDirectory $work -RedirectStandardOutput (Join-Path $work 'jtag_server.log') `
        -RedirectStandardError (Join-Path $work 'jtag_server.err') -PassThru
    Start-Sleep -Seconds 2
    Push-Location $work
    try {
        & "$bin\cdt_cfg_shell.exe" -file $script -work_dir $work *> (Join-Path $work 'jtag_program.log')
        $exitCode = $LASTEXITCODE
    } finally { Pop-Location }
    $log = Get-Content -LiteralPath (Join-Path $work 'jtag_program.log') -Raw
    if ($exitCode -ne 0 -or $log -notmatch 'COMMAND\[Program\] execute successfully' -or
        $log -notmatch 'FPGA_VOLATILE_PROGRAM_COMPLETE') { throw 'Volatile JTAG programming failed' }
    $record = [ordered]@{
        project = $manifest.project; project_source = $project; started_utc = $started.ToString('o')
        finished_utc = [DateTime]::UtcNow.ToString('o'); volatile_jtag = $true; flash_written = $false
        bitstream = $bitstreams[0].FullName; bitstream_sha256 = $manifest.bitstream_sha256
        manifest_sha256 = (Get-FileHash -LiteralPath $manifestPath).Hash
        vendor_work_directory = $work; staged_bitstream_sha256 = (Get-FileHash -LiteralPath $stagedBitstream).Hash
        frozen_source_written = $false
    }
    $record | ConvertTo-Json -Depth 6 | Set-Content -Encoding UTF8 (Join-Path $output 'program.json')
    Write-Output ('FROZEN_VOLATILE_PROGRAM_COMPLETE ' + ($record | ConvertTo-Json -Compress))
} finally {
    if ($server) {
        $server.Refresh()
        if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    }
    foreach ($name in @('program_volatile.tcl', 'jtag_program.log', 'jtag_server.log', 'jtag_server.err')) {
        $path = Join-Path $work $name
        if (Test-Path -LiteralPath $path) { Copy-Item -LiteralPath $path -Destination $output }
    }
}
