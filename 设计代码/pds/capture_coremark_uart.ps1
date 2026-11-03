param(
    [Parameter(Mandatory = $true)]
    [string]$PortName,
    [string]$ProjectName = 'riscv_coremark',
    [int]$ExpectedIterations = 300,
    [int]$ClockHz = 27000000
)

$ErrorActionPreference = 'Stop'
$work = Join-Path 'D:\FPGA_Work\projects' $ProjectName
$workForTcl = $work.Replace('\', '/')
$bin = 'D:\Pango\PDS_2022.2-SP6.4\bin'
$server = $null
$programmer = $null
$serial = $null
$capture = [System.Text.StringBuilder]::new()
$runHeader = '2K performance run parameters for coremark.'
$runStarted = $false
$discardedBytes = 0L

try {
    $serial = [System.IO.Ports.SerialPort]::new(
        $PortName, 115200, [System.IO.Ports.Parity]::None, 8,
        [System.IO.Ports.StopBits]::One)
    $serial.ReadTimeout = 1000
    $serial.Open()
    $serial.DiscardInBuffer()

    $serverArgs = @('-port', '65450', '-work_dir', $work)
    $server = Start-Process -FilePath "$bin\cdt_js.exe" -ArgumentList $serverArgs -WorkingDirectory $work -RedirectStandardOutput "$work\coremark_jtag_server.log" -RedirectStandardError "$work\coremark_jtag_server_error.log" -PassThru
    Start-Sleep -Seconds 2
    $server.Refresh()
    if ($server.HasExited) {
        throw 'JTAG server exited before programming.'
    }

    $programArgs = @('-file', "$workForTcl/program_coremark_volatile.tcl",
                     '-work_dir', $workForTcl)
    $programmer = Start-Process -FilePath "$bin\cdt_cfg_shell.exe" -ArgumentList $programArgs -WorkingDirectory $work -RedirectStandardOutput "$work\coremark_jtag_program.log" -RedirectStandardError "$work\coremark_jtag_program_error.log" -PassThru

    $deadline = [DateTime]::UtcNow.AddSeconds(180)
    $complete = $false
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $value = $serial.ReadByte()
            [void]$capture.Append([char]$value)
            if (-not $runStarted) {
                if ($capture.Length -gt $runHeader.Length) {
                    $remove = $capture.Length - $runHeader.Length
                    [void]$capture.Remove(0, $remove)
                    $discardedBytes += $remove
                }
                $runStarted = $capture.ToString() -ceq $runHeader
                if (-not $runStarted) { continue }
            }
            if ($capture.ToString() -match 'Correct operation validated\.[^\r\n]*[\r\n]') {
                $complete = $true
                break
            }
            if ($capture.ToString() -match 'Errors detected[\r\n]') {
                break
            }
        } catch [System.TimeoutException] { }
    }

    if (-not $programmer.WaitForExit(10000)) {
        throw 'JTAG programming timed out.'
    }
    $programLog = Get-Content "$work\coremark_jtag_program.log" -Raw
    $programSucceeded = $programLog -match 'COMMAND\[Program\] execute successfully' -and
        $programLog -match 'FPGA_VOLATILE_PROGRAM_COMPLETE'
    $exitCode = $programmer.ExitCode
    if (($null -ne $exitCode -and $exitCode -ne 0) -or -not $programSucceeded) {
        throw "JTAG programming failed (exit $exitCode, log success $programSucceeded)."
    }

    $result = $capture.ToString()
    if (-not $complete) {
        throw 'CoreMark did not report successful completion within 180 seconds.'
    }
    foreach ($pattern in @(
        '2K performance run parameters for coremark',
        'crclist\s*:\s*0xe714',
        'crcmatrix\s*:\s*0x1fd7',
        'crcstate\s*:\s*0x8e3a'
    )) {
        if ($result -notmatch $pattern) {
            throw "CoreMark output missing expected result: $pattern"
        }
    }
    if ($result -notmatch 'Total ticks\s*:\s*(\d+)') {
        throw 'CoreMark output missing total ticks.'
    }
    $ticks = [uint64]$Matches[1]
    if ($result -notmatch 'Iterations\s*:\s*(\d+)') {
        throw 'CoreMark output missing iteration count.'
    }
    $iterations = [uint64]$Matches[1]
    if ($iterations -ne $ExpectedIterations -or $ticks -lt ([uint64]$ClockHz * 10)) {
        throw "CoreMark duration is invalid: iterations=$iterations ticks=$ticks."
    }
    $score = [double]$iterations * $ClockHz / $ticks
    $scorePerMHz = [double]$iterations * 1000000 / $ticks
    Write-Output $result
    Write-Output "Discarded $discardedBytes bytes before the CoreMark run header."
    Write-Output ('PASS: ticks={0} iterations={1} CoreMark/s={2:F4} CoreMark/MHz={3:F4}' -f
        $ticks, $iterations, $score, $scorePerMHz)
} finally {
    Set-Content -Path "$work\coremark_uart.txt" -Value $capture.ToString() -Encoding Ascii
    if ($serial) {
        $serial.Close()
        $serial.Dispose()
    }
    if ($programmer) {
        $programmer.Refresh()
        if (-not $programmer.HasExited) {
            Stop-Process -Id $programmer.Id -Force -ErrorAction SilentlyContinue
        }
    }
    if ($server) {
        $server.Refresh()
        if (-not $server.HasExited) {
            Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
        }
    }
}
