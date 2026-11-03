param([Parameter(Mandatory = $true)][string]$ProjectName)
$ErrorActionPreference = 'Stop'
if ($ProjectName -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid project name' }
$work = Join-Path 'D:\FPGA_Work\projects' $ProjectName
$manifest = Get-Content -LiteralPath (Join-Path $work 'manifest.json') -Raw | ConvertFrom-Json
$bitstreams = @(Get-ChildItem -LiteralPath (Join-Path $work 'generate_bitstream') -Filter '*.sbit')
if ($bitstreams.Count -ne 1 -or -not $manifest.timing_met -or -not $manifest.volatile_only -or
    (Get-FileHash -LiteralPath $bitstreams[0].FullName).Hash -ne $manifest.bitstream_sha256) {
    throw 'Programming requires one verified volatile bitstream with timing closure'
}
foreach ($source in $manifest.sources) {
    $sourcePath = Join-Path $work $source.name
    if (-not (Test-Path -LiteralPath $sourcePath)) { $sourcePath = Join-Path (Join-Path $work 'src') $source.name }
    if ((Get-FileHash -LiteralPath $sourcePath).Hash -ne $source.sha256) {
        throw "Frozen source hash differs: $($source.name)"
    }
}
foreach ($constraint in $manifest.constraints) {
    if ((Get-FileHash -LiteralPath (Join-Path $work $constraint.name)).Hash -ne $constraint.sha256) {
        throw "Constraint hash differs: $($constraint.name)"
    }
}
$bin = 'D:\Pango\PDS_2022.2-SP6.4\bin'
$server = $null
$started = [DateTime]::UtcNow
$script = Join-Path $work ('program_volatile_' + $started.ToString('yyyyMMdd-HHmmss-fff') + '.tcl')
$bitstreamTcl = $bitstreams[0].FullName.Replace('\', '/')
@('cfg_connect -ip 127.0.0.1 -port 65450', 'cfg_scan_chain',
  ('cfg_assign_file -file {' + $bitstreamTcl + '} -device_index 0'),
  'cfg_program -device_index 0', 'puts "FPGA_VOLATILE_PROGRAM_COMPLETE"', 'cfg_disconnect') |
    Set-Content -Encoding Ascii $script
try {
    $server = Start-Process -FilePath "$bin\cdt_js.exe" -ArgumentList @('-port', '65450', '-work_dir', $work) `
        -WorkingDirectory $work -RedirectStandardOutput (Join-Path $work 'jtag_server.log') `
        -RedirectStandardError (Join-Path $work 'jtag_server.err') -PassThru
    Start-Sleep -Seconds 2
    Push-Location $work
    try {
        & "$bin\cdt_cfg_shell.exe" -file $script -work_dir $work *> (Join-Path $work 'jtag_program.log')
    } finally { Pop-Location }
    $log = Get-Content -LiteralPath (Join-Path $work 'jtag_program.log') -Raw
    if ($LASTEXITCODE -ne 0 -or $log -notmatch 'COMMAND\[Program\] execute successfully' -or
        $log -notmatch 'FPGA_VOLATILE_PROGRAM_COMPLETE') { throw 'Volatile JTAG programming failed' }
    [ordered]@{
        project = $ProjectName; started_utc = $started.ToString('o')
        finished_utc = [DateTime]::UtcNow.ToString('o'); volatile_jtag = $true; flash_written = $false
        bitstream = $bitstreams[0].FullName; bitstream_sha256 = $manifest.bitstream_sha256
    } | ConvertTo-Json | Set-Content -Encoding UTF8 (Join-Path $work 'program.json')
    Write-Output "VOLATILE_PROGRAM_COMPLETE $ProjectName $($manifest.bitstream_sha256)"
} finally {
    if ($server) {
        $server.Refresh()
        if (-not $server.HasExited) { Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue }
    }
}
