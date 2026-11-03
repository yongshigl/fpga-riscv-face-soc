param(
    [string]$ProjectName = 'vision_cpu81_ai54_v1',
    [string]$FirmwareDirectory = '',
    [string]$VendorDirectory = 'D:\FPGA_Work\vendor\ov5640_200k',
    [string]$VerifiedDdrDirectory = 'D:\FPGA_Work\projects\ddr_bist_vendor_clocks_20261001',
    [int]$PllFeedback = 18,
    [int]$CpuDivider = 12,
    [int]$AiDivider = 18,
    [ValidateSet(16, 32)][int]$MacPixels = 16,
    [string]$AcceleratorSource = '',
    [string]$DdrLinePortSource = '',
    [ValidateSet(2, 4, 8, 16, 32, 64)][int]$AiReadCacheLines = 2,
    [ValidateRange(1, 8)][int]$IcacheSetBits = 7,
    [ValidateRange(1, 8)][int]$DcacheSetBits = 6,
    [switch]$AiDepthwiseChannelOuter,
    [switch]$AiReadCacheFullLru,
    [switch]$AiDepthwiseWindowReuse,
    [switch]$AiQuantChannelPipeline,
    [ValidateSet(4, 8, 16, 32)][int]$AiWriteQueueDepth = 32,
    [switch]$Cameras,
    [string]$VendorCameraDirectory = 'D:\FPGA_Work\projects\camera_uart_diag\src',
    [ValidateRange(1, 63)][int]$JpegQuality = 8,
    [switch]$CameraOverlap,
    [switch]$RgbDatapath,
    [ValidateRange(1000, 120000)][int]$CameraCaptureLeadUs = 60000,
    [ValidateRange(1000, 60000)][int]$CameraMaxSendAgeUs = 30000,
    [switch]$OptimizeCachedCpu,
    [switch]$V64Cpu,
    [switch]$CounterCsrPredecode,
    [switch]$CacheIngressWrite,
    [switch]$CacheRegisterTags,
    [ValidateRange(0, 2)][int]$FetchPipeStages = 0,
    [ValidateRange(-1, 9)][int]$PlacementDecongestion = -1,
    [ValidateRange(-1, 2147483647)][int]$PlacementSeed = -1,
    [string]$PnrExtraArgs = ''
)
$ErrorActionPreference = 'Stop'
$optimizeCpu = $OptimizeCachedCpu -or $V64Cpu
if ($ProjectName -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Invalid project name' }
$repo = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..\..'))
if (-not $FirmwareDirectory) { $FirmwareDirectory = Join-Path $repo 'build\vision\firmware' }
$build = Join-Path 'D:\FPGA_Work\projects' $ProjectName
$bin = 'D:\Pango\PDS_2022.2-SP6.4\bin'
$env:PANGO_LICENSE_FILE = 'D:\Pango\License\new_pds_F4A80DA47D72.lic'
$firmware = Get-Content -LiteralPath (Join-Path $FirmwareDirectory 'firmware.json') -Raw | ConvertFrom-Json
$cpuHz = [int][Math]::Round(54000000.0 * $PllFeedback / $CpuDivider)
$aiHz = [int][Math]::Round(54000000.0 * $PllFeedback / $AiDivider)
if ($firmware.cpu_clock_hz -ne $cpuHz -or $firmware.ai_clock_hz -ne $aiHz) {
    throw 'Firmware and PLL clocks differ'
}
if ([bool]$firmware.features.camera_preview -ne [bool]$Cameras) {
    throw 'Firmware and camera hardware configuration differ'
}
if ([bool]$firmware.features.camera_tensor -ne [bool]$RgbDatapath) {
    throw 'Firmware and RGB datapath configuration differ'
}
if ($CameraOverlap -and -not $Cameras) { throw '-CameraOverlap requires -Cameras' }
if ($RgbDatapath -and -not $Cameras) { throw '-RgbDatapath requires -Cameras' }
if (Get-Process pds_shell -ErrorAction SilentlyContinue) { throw 'Another PDS build is running' }
if (Test-Path -LiteralPath (Join-Path $build 'manifest.json')) { throw 'Use a new name to preserve a completed project' }
$src = Join-Path $build 'src'
$mem = Join-Path $build 'mem'
New-Item -ItemType Directory -Force -Path $src, $mem | Out-Null
$rtl = @('uart_rx.v', 'cpu_data_port.v', 'two_way_cache.v', 'line_port_arbiter.v',
         'memory_line_backend.v', 'rv32i_cached_soc.v', 'transaction_cdc.v',
         'int8_mac_array.v', 'int8_requantize.v', 'int8_accelerator.v', 'vision_soc.v', 'ddr_line_port.v')
foreach ($name in $rtl) { Copy-Item -LiteralPath (Join-Path $repo ('rtl\' + $name)) -Destination $src }
if ($DdrLinePortSource) {
    $linePort = [IO.Path]::GetFullPath($DdrLinePortSource)
    if (-not (Test-Path -LiteralPath $linePort -PathType Leaf)) { throw 'DDR line-port source not found' }
    Copy-Item -LiteralPath $linePort -Destination (Join-Path $src 'ddr_line_port.v') -Force
}
if ($AcceleratorSource) {
    $accelerator = [IO.Path]::GetFullPath($AcceleratorSource)
    if (-not (Test-Path -LiteralPath $accelerator -PathType Leaf)) { throw 'Accelerator source not found' }
    Copy-Item -LiteralPath $accelerator -Destination (Join-Path $src 'int8_accelerator.v') -Force
}
Copy-Item -LiteralPath (Join-Path $repo 'rtl\rv32i_pipeline_core_lutram.s') -Destination (Join-Path $src 'rv32i_pipeline_core.v')
if ($V64Cpu) {
    Copy-Item -LiteralPath (Join-Path $repo 'rtl\rv32i_pipeline_core_trap_value.s') -Destination (Join-Path $src 'rv32i_pipeline_core.v') -Force
    Copy-Item -LiteralPath (Join-Path $repo 'rtl\rv32i_cached_soc_device_address.v') -Destination (Join-Path $src 'rv32i_cached_soc.v') -Force
}
Copy-Item -LiteralPath (Join-Path $repo 'rtl\uart_tx.s') -Destination (Join-Path $src 'uart_tx.v')
Copy-Item -LiteralPath (Join-Path $repo 'board\pg2l200h_mini\rtl\pg2l200h_mini_vision_top.v') -Destination $src
@"
``define VISION_PLL_FEEDBACK $PllFeedback
``define VISION_CPU_DIVIDER $CpuDivider
``define VISION_AI_DIVIDER $AiDivider
``define VISION_CPU_HZ $cpuHz
``define VISION_AI_HZ $aiHz
``define VISION_MAC_PIXELS $MacPixels
``define VISION_AI_READ_CACHE_LINES $AiReadCacheLines
``define VISION_ICACHE_SET_BITS $IcacheSetBits
``define VISION_DCACHE_SET_BITS $DcacheSetBits
``define VISION_AI_DEPTHWISE_CHANNEL_OUTER $([int][bool]$AiDepthwiseChannelOuter)
``define VISION_AI_READ_CACHE_FULL_LRU $([int][bool]$AiReadCacheFullLru)
``define VISION_AI_DEPTHWISE_WINDOW_REUSE $([int][bool]$AiDepthwiseWindowReuse)
``define VISION_AI_QUANT_CHANNEL_PIPELINE $([int][bool]$AiQuantChannelPipeline)
``define VISION_AI_WRITE_QUEUE_DEPTH $AiWriteQueueDepth
``define VISION_JPEG_QUALITY $JpegQuality
``define VISION_CAMERA_OVERLAP $([int][bool]$CameraOverlap)
``define VISION_CAMERA_CAPTURE_LEAD_US $CameraCaptureLeadUs
``define VISION_CAMERA_MAX_SEND_AGE_US $CameraMaxSendAgeUs
``define CPU_FETCH_PIPE_STAGES $FetchPipeStages
``define CPU_DECOUPLE_FETCH $([int][bool]$optimizeCpu)
``define CPU_SEPARATE_EXMEM_PAYLOAD $([int][bool]$optimizeCpu)
``define CPU_SEPARATE_FRONTEND_PAYLOAD $([int][bool]$optimizeCpu)
``define CPU_EARLY_BRANCH_CHECK $([int][bool]$optimizeCpu)
``define CPU_FAST_TRAP_PATH $([int][bool]$optimizeCpu)
``define CPU_FAST_REDIRECT_SELECT $([int][bool]$optimizeCpu)
``define CPU_COUNTER_CSR_PREDECODE $([int][bool]$CounterCsrPredecode)
``define CPU_CSR_ADDRESS_PREDECODE $([int][bool]$CounterCsrPredecode)
``define CPU_CACHE_FAST_RESPONSE $([int][bool]$optimizeCpu)
``define CPU_CACHE_EARLY_RESTART $([int][bool]$optimizeCpu)
``define CPU_CACHE_INGRESS_WRITE $([int][bool]$CacheIngressWrite)
``define CPU_CACHE_FAST_WRITE_RESPONSE $([int][bool]$optimizeCpu)
``define CPU_CACHE_REGISTER_TAGS $([int][bool]$CacheRegisterTags)
``define CPU_CACHE_STATISTICS 0
``define CPU_REGISTER_ICACHE_INVALIDATE $([int][bool]$optimizeCpu)
``define CPU_REGISTER_BYPASS $([int][bool]$optimizeCpu)
``define CPU_EARLY_LOAD_REQUEST $([int][bool]$optimizeCpu)
``define CPU_SPLIT_MTIME $([int][bool]$V64Cpu)
``define CPU_ALIGN_MMIO_ADDRESS $([int][bool]$V64Cpu)
``define CPU_REGFILE_LUTRAM
"@ | Set-Content -Encoding Ascii (Join-Path $src 'vision_config.vh')
foreach ($name in @('rv32i_pipeline_core.v', 'rv32i_cached_soc.v')) {
    $source = Get-Content -LiteralPath (Join-Path $src $name) -Raw
    ('`include "src/vision_config.vh"' + "`n" + $source) |
        Set-Content -Encoding Ascii (Join-Path $src $name)
}
if ($Cameras) {
    Add-Content -Encoding Ascii (Join-Path $src 'vision_config.vh') '`define VISION_CAMERAS'
    if ($RgbDatapath) {
        Add-Content -Encoding Ascii (Join-Path $src 'vision_config.vh') '`define VISION_RGB_DATAPATH'
    }
    foreach ($name in @('sccb_master.v', 'ov5640_control.v', 'jpeg_frame_capture.v', 'frame_capture.v',
                       'camera_packet_tx.v', 'camera_native_stream.v', 'camera_control_port.v',
                       'dvp_rgb565.v', 'camera_tensor_writer.v', 'camera_capture_gate.v',
                       'camera_frame_writer.v', 'camera_affine_resampler.v')) {
        Copy-Item -LiteralPath (Join-Path $repo ('board\pg2l200h_mini\camera_uart\' + $name)) -Destination $src
    }
    Copy-Item -LiteralPath (Join-Path $VendorCameraDirectory 'data_init.v') -Destination $src
}
foreach ($name in @('vision_text.hex', 'vision_data.hex')) {
    $path = Join-Path $FirmwareDirectory $name
    if ((Get-FileHash -LiteralPath $path).Hash -ne $firmware.files.$name) { throw "Firmware hash differs: $name" }
    Copy-Item -LiteralPath $path -Destination $mem
}
Copy-Item -LiteralPath (Join-Path $FirmwareDirectory 'firmware.json') -Destination $build
$ip = Join-Path $VendorDirectory 'prj\ipcore\DDR_200H'
$ipCopy = Join-Path $src 'DDR_200H'
New-Item -ItemType Directory -Force -Path $ipCopy | Out-Null
Copy-Item -LiteralPath (Join-Path $ip 'rtl') -Destination $ipCopy -Recurse -Force
foreach ($name in @('DDR_200H.v', 'DDR_200H_ddrphy_top.v', 'DDR_200H.idf')) {
    Copy-Item -LiteralPath (Join-Path $ip $name) -Destination $ipCopy
}
Copy-Item -LiteralPath (Join-Path $VerifiedDdrDirectory 'board_pins.fdc') -Destination $build
@'
define_attribute {p:uart_rx} {PAP_IO_DIRECTION} {INPUT}
define_attribute {p:uart_rx} {PAP_IO_LOC} {B24}
define_attribute {p:uart_rx} {PAP_IO_VCCIO} {3.3}
define_attribute {p:uart_rx} {PAP_IO_STANDARD} {LVCMOS33}
define_attribute {p:uart_rx} {PAP_IO_NONE} {TRUE}
'@ | Add-Content -Encoding Ascii (Join-Path $build 'board_pins.fdc')
if ($Cameras) {
    Get-Content -LiteralPath (Join-Path $repo 'board\pg2l200h_mini\camera_uart\camera_uart.fdc') |
        Where-Object { $_ -match '^define_attribute \{p:(camera_|SCL_|SDA_|cam_rst_)' } |
        Add-Content -Encoding Ascii (Join-Path $build 'board_pins.fdc')
}
$clocks = @(Get-Content -LiteralPath (Join-Path $VerifiedDdrDirectory 'ddr_clocks.fdc') | Where-Object {
    $_ -notmatch 'sys_async_ddr'
})
$clocks += 'create_generated_clock -name {cpu_clk} -source [get_ports {sys_clk}] [get_pins {clock_pll/CLKOUT0}] -master_clock [get_clocks {sys_clk}] ' +
    "-multiply_by {$($PllFeedback * 2)} -divide_by {$CpuDivider}"
$clocks += 'create_generated_clock -name {ai_clk} -source [get_ports {sys_clk}] [get_pins {clock_pll/CLKOUT1}] -master_clock [get_clocks {sys_clk}] ' +
    "-multiply_by {$($PllFeedback * 2)} -divide_by {$AiDivider}"
$domains = 'set_clock_groups -name {transaction_domains} -asynchronous -group [get_clocks {sys_clk}] -group [get_clocks {cpu_clk}] -group [get_clocks {ai_clk}] -group [get_clocks {ref_clk rst_clk ddrphy_sysclk phy_dq_clk_0 phy_dq_sysclk_0 phy_dq_clk_1 phy_dq_sysclk_1}]'
if ($Cameras) {
    $clocks += 'create_clock -name {camera_clk_0} [get_ports {camera_clk_0}] -period {10.000}'
    $clocks += 'create_clock -name {camera_clk_1} [get_ports {camera_clk_1}] -period {10.000}'
    $domains += ' -group [get_clocks {camera_clk_0}] -group [get_clocks {camera_clk_1}]'
}
$clocks += $domains
$clocks | Set-Content -Encoding Ascii (Join-Path $build 'vision_clocks.fdc')
$commands = @(Get-ChildItem -LiteralPath $src -File -Recurse | Where-Object { $_.Extension -in @('.v', '.vp') } |
    Sort-Object FullName | ForEach-Object { 'add_design "' + $_.FullName.Substring($build.Length + 1).Replace('\', '/') + '"' })
$routeCommand = 'pnr'
if ($PlacementDecongestion -ge 0) { $routeCommand += " -plc_decongestion $PlacementDecongestion" }
if ($PlacementSeed -ge 0) { $routeCommand += " -gplace_seed $PlacementSeed" }
if ($PnrExtraArgs) { $routeCommand += " $PnrExtraArgs" }
$commands += @('add_constraint "board_pins.fdc"', 'add_constraint "vision_clocks.fdc"',
    'set_arch -family Logos2 -device PG2L200H -speedgrade -6 -package FBB676',
    'compile -top_module pg2l200h_mini_vision_top', 'synthesize -ads -selected_syn_tool_opt 2',
    'dev_map', $routeCommand, 'report_timing', 'gen_bit_stream')
$commands | Set-Content -Encoding Ascii (Join-Path $build 'build_vision.tcl')
@'
cfg_connect -ip 127.0.0.1 -port 65450
cfg_scan_chain
cfg_assign_file -file generate_bitstream/pg2l200h_mini_vision_top.sbit -device_index 0
cfg_program -device_index 0
puts "FPGA_VOLATILE_PROGRAM_COMPLETE"
cfg_disconnect
'@ | Set-Content -Encoding Ascii (Join-Path $build 'program_volatile.tcl')
$started = [DateTime]::UtcNow
Write-Output "VISION_BUILD_STARTED $ProjectName $($started.ToString('o'))"
if (-not ('VisionBuildPower' -as [type])) {
    Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class VisionBuildPower { [DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint flags); }'
}
$previousExecutionState = [VisionBuildPower]::SetThreadExecutionState([uint32]2147483649)
if ($previousExecutionState -eq 0) { throw 'Unable to prevent automatic sleep during PDS' }
Push-Location $build
try {
    & "$bin\pds_shell.exe" -file build_vision.tcl *> (Join-Path $build 'build_console.log')
    if ($LASTEXITCODE -ne 0) { throw "PDS failed: $LASTEXITCODE" }
} finally {
    Pop-Location
    [VisionBuildPower]::SetThreadExecutionState($previousExecutionState) | Out-Null
}
$bitstream = Join-Path $build 'generate_bitstream\pg2l200h_mini_vision_top.sbit'
if (-not (Test-Path -LiteralPath $bitstream) -or (Get-Item -LiteralPath $bitstream).LastWriteTimeUtc -lt $started) {
    throw 'PDS did not produce a fresh vision bitstream'
}
$timing = Get-Content -LiteralPath (Join-Path $build 'report_timing\pg2l200h_mini_vision_top.rtr') -Raw
$route = Get-Content -LiteralPath (Join-Path $build 'place_route\pg2l200h_mini_vision_top.prr') -Raw
$resources = [regex]::Match($route,
    '(?m)^\|\s+pg2l200h_mini_vision_top\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+(\d+)\s+\|\s+([\d.]+)')
if (-not $resources.Success) { throw 'Missing PDS resource report' }
$manifest = [ordered]@{
    project = $ProjectName; built_utc = [DateTime]::UtcNow.ToString('o'); device = 'PG2L200H-6-FBB676'
    cpu_clock_hz = $cpuHz; ai_clock_hz = $aiHz; memory_clock_hz = 125000000; uart_baud = 921600
    firmware = $firmware
    icache_bytes = 64 * (1 -shl $IcacheSetBits); dcache_bytes = 64 * (1 -shl $DcacheSetBits)
    mac_pixels = $MacPixels; logical_tile_pixels = 32; mac_channels = 16
    ai_read_cache_lines = $AiReadCacheLines
    icache_set_bits = $IcacheSetBits; dcache_set_bits = $DcacheSetBits
    ai_depthwise_channel_outer = [bool]$AiDepthwiseChannelOuter
    ai_read_cache_full_lru = [bool]$AiReadCacheFullLru
    ai_depthwise_window_reuse = [bool]$AiDepthwiseWindowReuse
    ai_quant_channel_pipeline = [bool]$AiQuantChannelPipeline
    ai_write_queue_depth = $AiWriteQueueDepth
    camera_preview = [bool]$Cameras
    rgb_datapath = [bool]$RgbDatapath
    camera_format = $(if ($RgbDatapath) { 'OV5640 RGB565 1280x720 on-board tensor' } elseif ($Cameras) { 'OV5640 JPEG 640x360' } else { $null })
    jpeg_quantization_scale = $(if ($Cameras) { $JpegQuality } else { $null })
    camera_overlap_capture = [bool]$CameraOverlap
    camera_capture_lead_us = $CameraCaptureLeadUs; camera_max_send_age_us = $CameraMaxSendAgeUs
    cached_cpu_optimized = [bool]$optimizeCpu
    v64_cpu = [bool]$V64Cpu
    cpu_core = $(if ($V64Cpu) { 'rv32i_pipeline_core_trap_value.s' } else { 'rv32i_pipeline_core_lutram.s' })
    cpu_soc = $(if ($V64Cpu) { 'rv32i_cached_soc_device_address.v' } else { 'rv32i_cached_soc.v' })
    counter_csr_predecode = [bool]$CounterCsrPredecode
    cache_ingress_write = [bool]$CacheIngressWrite
    cache_register_tags = [bool]$CacheRegisterTags
    accelerator_override = [bool]$AcceleratorSource
    placement_decongestion = $(if ($PlacementDecongestion -ge 0) { $PlacementDecongestion } else { $null })
    placement_seed = $(if ($PlacementSeed -ge 0) { $PlacementSeed } else { $null })
    fetch_pipe_stages = $FetchPipeStages; pipeline_stages = 5 + $FetchPipeStages
    external_dvp_input_delays_characterized = $false
    timing_met = $timing.Contains('Design Summary : All Constraints Met.')
    lut_post_route = [int]$resources.Groups[1].Value; ff_post_route = [int]$resources.Groups[2].Value
    distributed_ram_luts = [int]$resources.Groups[3].Value; apm = [int]$resources.Groups[4].Value
    drm = [double]$resources.Groups[5].Value; bitstream_sha256 = (Get-FileHash -LiteralPath $bitstream).Hash
    volatile_only = $true; clock_independently_measured = $false
    sources = @(Get-ChildItem -LiteralPath $src -File -Recurse | ForEach-Object {
        [ordered]@{ name = $_.FullName.Substring($build.Length + 1); sha256 = (Get-FileHash -LiteralPath $_.FullName).Hash }
    })
    constraints = @('board_pins.fdc', 'vision_clocks.fdc') | ForEach-Object {
        [ordered]@{ name = $_; sha256 = (Get-FileHash -LiteralPath (Join-Path $build $_)).Hash }
    }
}
$manifest | ConvertTo-Json -Depth 8 | Set-Content -Encoding UTF8 (Join-Path $build 'manifest.json')
Write-Output "VISION_BUILD_COMPLETE timing_met=$($manifest.timing_met) LUT=$($manifest.lut_post_route) APM=$($manifest.apm) DRM=$($manifest.drm)"
