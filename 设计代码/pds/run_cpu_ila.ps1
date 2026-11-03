param(
    [ValidateSet('Build', 'Capture')][string]$Action = 'Build',
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$ProjectName = 'riscv_target_btb64_120m_ila_named'
)
$ErrorActionPreference = 'Stop'
$work = Join-Path 'D:\FPGA_Work\projects' $ProjectName
$bin = 'D:\Pango\PDS_2022.2-SP6.4\bin'
$env:PANGO_LICENSE_FILE = 'D:\Pango\License\new_pds_F4A80DA47D72.lic'
$configuration = Get-Content -LiteralPath (Join-Path $work 'ila_configuration.json') -Raw | ConvertFrom-Json
$bitstream = Join-Path $work 'generate_bitstream\pg2l200h_mini_coremark_top.sbit'

if ($Action -eq 'Build') {
    if (Get-Process pds_shell -ErrorAction SilentlyContinue) { throw 'Another PDS build is running' }
    if (Test-Path -LiteralPath (Join-Path $work 'manifest.json')) { throw 'Preserve the completed ILA project' }
    $sourceProject = Join-Path 'D:\FPGA_Work\projects' $configuration.source_project
    $manifest = Get-Content -LiteralPath (Join-Path $sourceProject 'manifest.json') -Raw | ConvertFrom-Json
    foreach ($source in $manifest.sources) {
        if ((Get-FileHash -LiteralPath (Join-Path $work ('src\' + $source.name))).Hash -ne $source.sha256) {
            throw "ILA source differs from the original CPU project: $($source.name)"
        }
    }
    $started = [DateTime]::UtcNow
    Write-Output "ILA_BUILD_STARTED $ProjectName $($started.ToString('o'))"
    Push-Location $work
    try {
        & "$bin\pds_shell.exe" -file build_ila.tcl *> (Join-Path $work 'build_console.log')
        if ($LASTEXITCODE -ne 0) { throw "ILA PDS build failed: $LASTEXITCODE" }
    } finally { Pop-Location }
    if (-not (Test-Path -LiteralPath $bitstream) -or (Get-Item -LiteralPath $bitstream).LastWriteTimeUtc -lt $started) {
        throw 'PDS did not produce a fresh ILA bitstream'
    }
    $timing = Get-Content -LiteralPath (Join-Path $work 'report_timing\pg2l200h_mini_coremark_top.rtr') -Raw
    $route = Get-Content -LiteralPath (Join-Path $work 'place_route\pg2l200h_mini_coremark_top.prr') -Raw
    $synthesis = Get-Content -LiteralPath (Join-Path $work 'synthesize\run.log') -Raw
    $resources = [regex]::Match($route,
        '(?m)^\|\s+pg2l200h_mini_coremark_top\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+([\d.]+)')
    $lut = [regex]::Match($synthesis, 'Total LUTs:\s+(\d+)')
    if (-not $resources.Success -or -not $lut.Success) { throw 'Missing ILA resource reports' }
    $manifest.project = $ProjectName
    $manifest.built_utc = [DateTime]::UtcNow.ToString('o')
    $manifest.timing_met = $timing.Contains('Design Summary : All Constraints Met.')
    $manifest.lut_synthesis = [int]$lut.Groups[1].Value
    $manifest.lut_post_route = [int]$resources.Groups[1].Value
    $manifest.ff_post_route = [int]$resources.Groups[2].Value
    $manifest.distributed_ram_luts = [int]$resources.Groups[3].Value
    $manifest.apm = [int]$resources.Groups[4].Value
    $manifest.drm = [double]$resources.Groups[5].Value
    $manifest.bitstream_sha256 = (Get-FileHash -LiteralPath $bitstream).Hash
    $manifest.sources = @($manifest.sources) + @([ordered]@{
        name = 'cpu_ila.fic'; sha256 = (Get-FileHash -LiteralPath (Join-Path $work 'cpu_ila.fic')).Hash
    })
    $manifest | Add-Member -NotePropertyName ila -NotePropertyValue $configuration -Force
    $manifest | Add-Member -NotePropertyName constraints -NotePropertyValue @(
        @('pg2l200h_mini_soc.fdc', 'cpu_clock.fdc') | ForEach-Object {
            [ordered]@{ name = $_; sha256 = (Get-FileHash -LiteralPath (Join-Path $work $_)).Hash }
        }
    ) -Force
    Copy-Item -LiteralPath (Join-Path $work 'program_coremark_volatile.tcl') -Destination (Join-Path $work 'program_volatile.tcl')
    $manifest | ConvertTo-Json -Depth 10 | Set-Content -Encoding UTF8 (Join-Path $work 'manifest.json')
    Write-Output "ILA_BUILD_COMPLETE timing_met=$($manifest.timing_met) LUT=$($manifest.lut_post_route)"
}

if ($Action -eq 'Capture') {
    & (Join-Path $PSScriptRoot 'program_project.ps1') -ProjectName $ProjectName
    $capture = Join-Path $work ('capture_' + [DateTime]::UtcNow.ToString('yyyyMMdd_HHmmss'))
    New-Item -ItemType Directory -Path $capture | Out-Null
    $projectFile = (Join-Path $capture 'cpu_pipeline.dprj').Replace('\', '/')
    $fic = (Join-Path $work 'cpu_ila.fic').Replace('\', '/')
    @('dbg_connect -ip 127.0.0.1 -port 65450', 'dbg_scan_chain',
      'dbg_set_cur_core -device 0 -core 0', ('dbg_import_fic -file ' + $fic),
      'dbg_add_all_to_waveform', 'dbg_add_all_to_listing', 'dbg_power_init',
      ('dbg_save_project -file ' + $projectFile), 'dbg_disconnect', 'dbg_close') |
        Set-Content -Encoding Ascii (Join-Path $capture 'capture.tcl')
    $server = $debugger = $null
    try {
        $server = Start-Process -FilePath "$bin\cdt_js.exe" -ArgumentList @('-port', '65450', '-work_dir', $work) `
            -WorkingDirectory $work -RedirectStandardOutput (Join-Path $capture 'jtag.log') `
            -RedirectStandardError (Join-Path $capture 'jtag.err') -PassThru
        Start-Sleep -Seconds 2
        $debugger = Start-Process -FilePath "$bin\cdt_dbg.exe" -ArgumentList @('-file', (Join-Path $capture 'capture.tcl')) `
            -WorkingDirectory $capture -RedirectStandardOutput (Join-Path $capture 'debugger.log') `
            -RedirectStandardError (Join-Path $capture 'debugger.err') -PassThru
        $deadline = [DateTime]::UtcNow.AddSeconds(90)
        do {
            Start-Sleep -Milliseconds 250
            $debugger.Refresh()
            $saved = Test-Path -LiteralPath $projectFile
        } while (-not $saved -and -not $debugger.HasExited -and [DateTime]::UtcNow -lt $deadline)
        if (-not $saved) { throw 'ILA did not save a capture; inspect debugger.log and debugger.err' }
        Start-Sleep -Seconds 1
        $waveforms = @(Get-ChildItem -LiteralPath $capture -Filter '*.wdf')
        if (-not $waveforms.Count -or -not ($waveforms | Where-Object Length -gt 0)) {
            throw 'ILA project was saved without waveform data'
        }
        [ordered]@{
            captured_utc = [DateTime]::UtcNow.ToString('o'); execution = 'Fabric Debugger JTAG power-on ILA readback'
            project = $ProjectName; bitstream_sha256 = (Get-FileHash -LiteralPath $bitstream).Hash
            trigger_pc = $configuration.trigger_pc; clock_hz = (Get-Content -LiteralPath (Join-Path $work 'manifest.json') -Raw | ConvertFrom-Json).clock_hz
            volatile_jtag = $true; flash_written = $false
            files = @($waveforms | ForEach-Object { [ordered]@{ name = $_.Name; bytes = $_.Length; sha256 = (Get-FileHash -LiteralPath $_.FullName).Hash } })
            waveform_content_verified = $false
        } | ConvertTo-Json -Depth 5 | Set-Content -Encoding UTF8 (Join-Path $capture 'capture.json')
        Write-Output "ILA_CAPTURE_SAVED $capture"
    } finally {
        foreach ($process in @($debugger, $server)) {
            if ($process) {
                $process.Refresh()
                if (-not $process.HasExited) { Stop-Process -Id $process.Id }
            }
        }
    }
}
