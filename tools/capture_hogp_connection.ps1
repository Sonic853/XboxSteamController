param(
    [ValidateRange(15,300)][int]$Seconds = 90,
    [string]$Address,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\analysis'),
    [switch]$Pairing
)
$ErrorActionPreference = 'Stop'
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run the xbox-hogp trace BAT as administrator.'
}
if ($Pairing -and -not $PSBoundParameters.ContainsKey('Seconds')) { $Seconds = 300 }
if ($Address) {
    $Address = ($Address -replace '[:-]', '').ToUpperInvariant()
    if ($Address -notmatch '^[0-9A-F]{12}$') { throw 'Invalid Bluetooth address.' }
}
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$mode = if ($Pairing) { 'PAIRING' } else { 'TIMED' }
$trace = 'XSC-HOGP-' + $mode + '-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$base = Join-Path $OutputDirectory $trace
$log = $base + '.log'
$reader = Join-Path $PSScriptRoot 'read_ble_diagnostics.ps1'
$started = $false
$captureStart = $null
$captureStop = $null
$diagnosticCode = $null
$failure = $null

function Write-TraceLog([string]$Text) {
    $Text | Tee-Object -FilePath $log -Append | Out-Host
}
function Invoke-LoggedNative([string]$Command, [string[]]$Arguments) {
    Write-TraceLog ("> $Command " + ($Arguments -join ' '))
    $lines = & $Command @Arguments 2>&1
    $code = $LASTEXITCODE
    $lines | ForEach-Object { Write-TraceLog ([string]$_) }
    Write-TraceLog "Exit code: $code"
    return $code
}

try {
    $code = Invoke-LoggedNative 'logman.exe' @('create', 'trace', $trace, '-p',
        '{0107cf95-313a-473e-9078-e73cd932f2fe}', '0xFFFFFFFF', '5',
        '-o', ($base + '.etl'), '-f', 'bincirc', '-max', '16', '-ets')
    if ($code -ne 0) { throw "Trace could not start (exit $code)." }
    $started = $true
    $captureStart = [DateTime]::UtcNow
    Write-TraceLog "RECORDING STARTED: $($captureStart.ToString('o'))"
    Write-TraceLog "Recording automatically for $Seconds seconds, then reading firmware diagnostics."
    if ($Pairing) {
        Write-TraceLog 'Goal: capture fresh HID report discovery and FIRST subscription during pairing.'
        Write-TraceLog '1. NOW turn this Steam Controller off.'
        Write-TraceLog '2. In Windows Bluetooth settings, remove ONLY its Xbox Wireless Controller entry.'
        Write-TraceLog '3. Wait until removal finishes. Hold RB + Y and press Steam to enter Xbox BLE pairing.'
        Write-TraceLog '4. Add Bluetooth device > Xbox Wireless Controller. Keep this capture window open.'
    } else {
        Write-TraceLog 'NOW turn the controller off, then reconnect with RB + Steam.'
    }
    Write-TraceLog 'Open joy.cpl Properties / Test. Press buttons and move the stick; keep the controller connected.'
    Write-TraceLog 'No key press is needed to stop. Wait for FINISHED in this window.'
    # A key or gamepad-generated keystroke cannot shorten this recording window.
    Start-Sleep -Seconds $Seconds
    Write-TraceLog 'Reading firmware diagnostics WHILE THE TRACE IS STILL RECORDING...'
    $arguments = @('-NoProfile', '-File', $reader, '-Samples', '2', '-OutputPath', ($base + '.json'))
    if ($Address) { $arguments += @('-Address', $Address) }
    $diagnosticCode = Invoke-LoggedNative 'powershell.exe' $arguments
    if ($diagnosticCode -ne 0) { Write-TraceLog 'Firmware diagnostics failed; saving the ETL anyway.' }
} catch {
    $failure = $_
    Write-TraceLog "Capture error: $($_.Exception.Message)"
} finally {
    if ($started) {
        $stopCode = Invoke-LoggedNative 'logman.exe' @('stop', $trace, '-ets')
        if ($stopCode -ne 0) {
            throw "Trace stop failed. In an administrator terminal run: logman stop $trace -ets"
        }
        $captureStop = [DateTime]::UtcNow
        Write-TraceLog "TRACE STOPPED AND SAVED: $base.etl"
    }
}
if ($failure) { throw $failure }

# Verify the saved trace contains driver events rather than only an ETL header.
$exportCode = Invoke-LoggedNative 'tracerpt.exe' @(($base + '.etl'), '-o', ($base + '.events.xml'),
    '-of', 'XML', '-summary', ($base + '.summary.txt'), '-y')
$wppCount = $null
if ($exportCode -eq 0) {
    $xmlReader = [System.Xml.XmlReader]::Create($base + '.events.xml')
    try {
        $wppCount = 0
        while ($xmlReader.Read()) {
            if ($xmlReader.NodeType -eq [System.Xml.XmlNodeType]::Element -and $xmlReader.LocalName -eq 'DebugData') {
                $wppCount++
            }
        }
    } finally { $xmlReader.Dispose() }
    Write-TraceLog "HOGP WPP events saved: $wppCount"
    if ($wppCount -eq 0) { Write-TraceLog 'NO DRIVER EVENTS: the ETL is saved, but it does not show a HID connection/subscription.' }
} else {
    Write-TraceLog 'Event conversion failed; the original ETL is saved for offline analysis.'
}
[ordered]@{
    trace = $trace
    mode = $mode.ToLowerInvariant()
    startedUtc = $captureStart.ToString('o')
    stoppedUtc = $captureStop.ToString('o')
    requestedTestSeconds = $Seconds
    elapsedSeconds = ($captureStop - $captureStart).TotalSeconds
    diagnosticsExitCode = $diagnosticCode
    exportExitCode = $exportCode
    wppEvents = $wppCount
} | ConvertTo-Json | Set-Content -Encoding UTF8 ($base + '.capture.json')
Write-TraceLog "FINISHED. Recording files: $base"
