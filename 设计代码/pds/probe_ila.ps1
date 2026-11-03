param([string]$ProjectName = 'riscv_target_btb64_120m')
$ErrorActionPreference = 'Stop'
if ($ProjectName -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid project name' }
$project = Join-Path 'D:\FPGA_Work\projects' $ProjectName
$work = Join-Path 'D:\FPGA_Work\projects' ($ProjectName + '_ila_probe')
New-Item -ItemType Directory -Force -Path $work | Out-Null
$env:PANGO_LICENSE_FILE = 'D:\Pango\License\new_pds_F4A80DA47D72.lic'
$netlist = Join-Path $project 'synthesize\pg2l200h_mini_coremark_top_syn.adf'
$fic = Join-Path $work 'probe.fic'
$commands = Get-Content -LiteralPath (Join-Path $PSScriptRoot 'probe_ila.tcl') -Raw
$commands.Replace('@PROJECT@', $fic.Replace('\', '/')).Replace('@NETLIST@', $netlist.Replace('\', '/')) |
    Set-Content -Encoding Ascii (Join-Path $work 'probe_ila.tcl')
$started = [DateTime]::UtcNow
Push-Location $work
try {
    $process = Start-Process -FilePath 'D:\Pango\PDS_2022.2-SP6.4\bin\cdt_ins.exe' `
        -ArgumentList @('-device', 'PG2L200H', '-package', 'FBB676', '-speedgrade', '-6',
                        '-netlist', $netlist, '-file', 'probe_ila.tcl') `
        -WorkingDirectory $work -RedirectStandardOutput (Join-Path $work 'probe.log') `
        -RedirectStandardError (Join-Path $work 'probe.err') -PassThru
    $deadline = $started.AddSeconds(360)
    $log = Join-Path $work 'log\ins.log'
    do {
        Start-Sleep -Milliseconds 200
        $saved = (Test-Path -LiteralPath $fic) -and (Get-Item -LiteralPath $fic).LastWriteTimeUtc -ge $started -and
            (Test-Path -LiteralPath $log) -and (Select-String -LiteralPath $log -SimpleMatch 'Executing : ins_core_info')
        if ($process.HasExited -and -not $saved) { throw "Inserter exited before saving the probe: $($process.ExitCode)" }
    } while (-not $saved -and [DateTime]::UtcNow -lt $deadline)
    if (-not $saved) { throw 'Inserter probe timed out; inspect log\ins.log' }
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id }
    Pop-Location
}
Write-Output "ILA_PROBE_COMPLETE $work"
