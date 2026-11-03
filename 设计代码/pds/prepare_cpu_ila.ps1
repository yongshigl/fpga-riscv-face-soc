param(
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$ProjectName = 'riscv_target_btb64_120m',
    [ValidatePattern('^[a-zA-Z0-9_-]+$')][string]$OutputName = 'riscv_target_btb64_120m_ila',
    [uint32]$TriggerPc = 0x00000c64
)
$ErrorActionPreference = 'Stop'
$project = Join-Path 'D:\FPGA_Work\projects' $ProjectName
$probe = Join-Path 'D:\FPGA_Work\projects' ($ProjectName + '_ila_probe')
$work = Join-Path 'D:\FPGA_Work\projects' $OutputName
if (Test-Path -LiteralPath (Join-Path $work 'cpu_ila.fic')) { throw 'Use a new ILA project name' }
$nets = @{}
foreach ($line in Get-Content -LiteralPath (Join-Path $probe 'log\ins.log')) {
    if ($line -match '^\s*(\d+)\s*:\s*(\S+)\s+') { $nets[$matches[2]] = $true }
}
function Net([string]$Name) {
    if (-not $nets.ContainsKey($Name)) { throw "Missing synthesis net: $Name" }
    return $Name
}
$trigger = @(0..31 | ForEach-Object { Net "soc/core/idex_pc[$_]" })
$data = @(0..31 | ForEach-Object { Net "soc/core/ifid_pc[$_]" }) + $trigger
$flags = @('ifid_valid', 'idex_valid', 'exmem_valid', 'memwb_valid',
           'load_use_stall', 'ex_mispredict', 'ex_branch_taken', 'take_trap')
$data += @($flags | ForEach-Object { Net ('soc/core/' + $_) })
New-Item -ItemType Directory -Force -Path $work | Out-Null
$fic = (Join-Path $work 'cpu_ila.fic').Replace('\', '/')
$netlist = (Join-Path $project 'synthesize\pg2l200h_mini_coremark_top_syn.adf').Replace('\', '/')
# Inserter net indexes change between processes. FIC stores stable signal names.
$configuration = [ordered]@{
    'Project.type' = 'inserter'
    'Project.device.designInputFile' = $netlist
    'Project.device.ScanChain' = '1'
    'Project.device.UserJtag' = '0'
    'Project.Architecture' = '1 1 1'
    'Project.unit.dimension' = '1'
    'Project.unit<0>.clockChannel' = (Net 'cpu_clk')
    'Project.unit<0>.clockEdge' = 'Rising'
    'Project.unit<0>.ramType' = '1'
    'Project.unit<0>.dataDepth' = '2048'
    'Project.unit<0>.dataEqualsTrigger' = 'false'
    'Project.unit<0>.dataPortWidth' = '72'
    'Project.unit<0>.enablePowerOnInitData' = 'true'
    'Project.unit<0>.enableStorageQualification' = 'false'
    'Project.unit<0>.triggerSequencerLevels' = '1'
    'Project.unit<0>.enableMultiWindow' = 'false'
    'Project.unit<0>.triggerPortCount' = '1'
    'Project.unit<0>.triggerMatchCount<0>' = '1'
    'Project.unit<0>.triggerMatchCountWidth<0>' = '0'
    'Project.unit<0>.triggerMatchType<0>' = '0'
    'Project.unit<0>.triggerPortIsData<0>' = 'true'
    'Project.unit<0>.triggerPortWidth<0>' = '32'
    'Project.unit<0>.MatchUnitFunction<0>' = '0'
    'Project.unit<0>.MatchUnitRange<0>' = ('0' * 32 + ',' + '0' * 32)
    'Project.unit<0>.MatchUnitValue<0>' = [Convert]::ToString($TriggerPc, 2).PadLeft(32, '0')
    'Project.unit<0>.MatchUnitRadix<0>' = '0'
    'Project.unit<0>.MatchUnitCounterType<0>' = '0'
    'Project.unit<0>.MatchUnitCounterCycles<0>' = '1'
    'Project.unit<0>.TriggerConditionType<0>' = '0'
    'Project.unit<0>.TriggerCondition<0>' = 'TU0'
    'Project.unit<0>.TriggerConditionName<0>' = 'TriggerCondition0'
    'Project.unit<0>.StorageType' = '0'
    'Project.unit<0>.StorageWindows' = '1'
    'Project.unit<0>.StoragePosition' = '256'
    'Project.unit<0>.StorageSamples' = '512'
}
for ($i = 0; $i -lt $data.Count; $i++) {
    $configuration.Add("Project.unit<0>.dataChannel<$i>", $data[$i])
}
for ($i = 0; $i -lt $trigger.Count; $i++) {
    $configuration.Add("Project.unit<0>.triggerChannel<0><$i>", $trigger[$i])
}
@('#Fabric Core Inserter Project File') + @($configuration.GetEnumerator() | ForEach-Object {
    $_.Key + '=' + $_.Value
}) | Set-Content -Encoding Ascii -LiteralPath $fic
$commands = @("ins_open $fic", 'ins_core_info -core 0 -trig 0 -unit 0',
              'ins_save', 'ins_core_info -core 0 -trig 0')
$commands | Set-Content -Encoding Ascii (Join-Path $work 'insert.tcl')
$env:PANGO_LICENSE_FILE = 'D:\Pango\License\new_pds_F4A80DA47D72.lic'
$started = [DateTime]::UtcNow
$process = $null
try {
    $process = Start-Process -FilePath 'D:\Pango\PDS_2022.2-SP6.4\bin\cdt_ins.exe' `
        -ArgumentList @('-device', 'PG2L200H', '-package', 'FBB676', '-speedgrade', '-6', '-file', 'insert.tcl') `
        -WorkingDirectory $work -RedirectStandardOutput (Join-Path $work 'insert.log') `
        -RedirectStandardError (Join-Path $work 'insert.err') -PassThru
    $deadline = $started.AddSeconds(360)
    $log = Join-Path $work 'log\ins.log'
    do {
        Start-Sleep -Milliseconds 200
        $complete = (Test-Path -LiteralPath $log) -and
            (Select-String -LiteralPath $log -SimpleMatch 'Executing : ins_core_info -core 0 -trig 0')
        if ($process.HasExited -and -not $complete) { throw 'Inserter exited before completing the ILA configuration' }
    } while (-not $complete -and [DateTime]::UtcNow -lt $deadline)
    if (-not $complete -or -not (Test-Path -LiteralPath $fic)) { throw 'ILA configuration failed; inspect log\ins.log' }
    $saved = @{}
    foreach ($line in Get-Content -LiteralPath $fic) {
        $separator = $line.IndexOf('=')
        if ($separator -gt 0) { $saved[$line.Substring(0, $separator)] = $line.Substring($separator + 1) }
    }
    foreach ($entry in $configuration.GetEnumerator()) {
        if ($saved[$entry.Key].Trim() -ne $entry.Value) {
            throw "Inserter changed ILA configuration: $($entry.Key) expected $($entry.Value), got $($saved[$entry.Key])"
        }
    }
} finally {
    if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id }
}
foreach ($name in @('src', 'mem')) { Copy-Item -LiteralPath (Join-Path $project $name) -Destination $work -Recurse }
foreach ($name in @('firmware.json', 'pg2l200h_mini_soc.fdc', 'cpu_clock.fdc', 'program_coremark_volatile.tcl')) {
    Copy-Item -LiteralPath (Join-Path $project $name) -Destination $work
}
$build = @(Get-Content -LiteralPath (Join-Path $project 'build_cpu.tcl'))
@('add_fic "cpu_ila.fic"') + $build | Set-Content -Encoding Ascii (Join-Path $work 'build_ila.tcl')
[ordered]@{
    source_project = $ProjectName; project = $OutputName; trigger_pc = $TriggerPc
    depth = 2048; pretrigger_samples = 256; power_on_capture = $true
    clock = 'cpu_clk'; data_names = @('ifid_pc', 'idex_pc') + $flags
    channel_bits = 72; source_bitstream_sha256 = (Get-Content -LiteralPath (Join-Path $project 'manifest.json') -Raw | ConvertFrom-Json).bitstream_sha256
} | ConvertTo-Json -Depth 3 | Set-Content -Encoding UTF8 (Join-Path $work 'ila_configuration.json')
Write-Output "CPU_ILA_PREPARED $work"
