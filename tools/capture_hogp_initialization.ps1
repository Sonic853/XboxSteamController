param(
    [string]$Address,
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\analysis')
)
$ErrorActionPreference = 'Stop'

# Capture a new Windows HOGP adapter instance, rather than only a radio reconnect.
# Restart only this controller's HID service; retain its pairing and firmware.
$principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run xbox-hogp-init-trace.bat as administrator.'
}
if ($Address) {
    $Address = ($Address -replace '[:-]', '').ToUpperInvariant()
    if ($Address -notmatch '^[0-9A-F]{12}$') { throw 'Invalid Bluetooth address.' }
}
$pattern = '^BTHLEDEVICE\\\{00001812-0000-1000-8000-00805F9B34FB\}_DEV_VID&02045E_PID&0B13_REV&[0-9A-F]{4}_([0-9A-F]{12})\\'
$targets = @(Get-PnpDevice -PresentOnly | Where-Object {
    if ($_.InstanceId -match $pattern) {
        (-not $Address) -or ($Matches[1] -eq $Address)
    }
})
if ($targets.Count -ne 1) {
    throw "Found $($targets.Count) matching Xbox BLE HID services. Specify -Address to select one."
}
$target = $targets[0]
$null = $target.InstanceId -match $pattern
$Address = $Matches[1]
$null = New-Item -ItemType Directory -Path $OutputDirectory -Force
$OutputDirectory = (Resolve-Path $OutputDirectory).Path
$trace = 'XSC-HOGP-INIT-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0, 8)
$base = Join-Path $OutputDirectory $trace
$log = $base + '.log'
$reader = Join-Path $PSScriptRoot 'read_ble_diagnostics.ps1'
$started = $false
$restartCode = $null
$restartRequired = $false
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

Write-TraceLog "Started UTC: $([DateTime]::UtcNow.ToString('o'))"
Write-TraceLog "Controller: $Address"
Write-TraceLog "HID service: $($target.InstanceId)"
try {
    $code = Invoke-LoggedNative 'logman.exe' @('create', 'trace', $trace, '-p',
        '{0107cf95-313a-473e-9078-e73cd932f2fe}', '0xFFFFFFFF', '5',
        '-o', ($base + '.etl'), '-f', 'bincirc', '-max', '16', '-ets')
    if ($code -ne 0) { throw "Trace could not start (exit $code)." }
    $started = $true
    Write-TraceLog 'Restarting this controller''s Windows HID service...'
    $restartCode = Invoke-LoggedNative 'pnputil.exe' @('/restart-device', $target.InstanceId)
    if ($restartCode -eq 3010) {
        $restartRequired = $true
        Write-TraceLog 'Windows requires a system restart to complete the device restart (3010).'
    } else {
        if ($restartCode -ne 0) { throw "HID service restart did not finish (exit $restartCode). See the log." }
        Write-TraceLog 'Keep the controller on. Open joy.cpl Properties / Test and press several buttons.'
        Write-TraceLog 'If disconnected, reconnect with RB + Steam, then test.'
        $null = Read-Host 'After testing, press ENTER HERE to stop and save the trace'
    }
} catch {
    $failure = $_
    Write-TraceLog "Capture error: $($_.Exception.Message)"
} finally {
    if ($started) {
        $stopCode = Invoke-LoggedNative 'logman.exe' @('stop', $trace, '-ets')
        if ($stopCode -ne 0) {
            throw "Trace stop failed. In an administrator terminal run: logman stop $trace -ets"
        }
        Write-TraceLog "TRACE STOPPED AND SAVED: $base.etl"
    }
}
if ($failure) { throw $failure }
if ($restartRequired) {
    Write-TraceLog 'Turn the controller OFF, save your work, and restart Windows manually.'
    Write-TraceLog 'After reboot, keep the controller OFF until xbox-hogp-trace.bat has started recording.'
    Write-TraceLog 'Then connect with RB + Steam, test in joy.cpl, and wait for automatic saving / FINISHED.'
    exit 3010
}
Write-TraceLog 'Reading firmware diagnostics...'
$diagnosticCode = Invoke-LoggedNative 'powershell.exe' @('-NoProfile', '-File', $reader,
    '-Address', $Address, '-Samples', '2', '-OutputPath', ($base + '.json'))
if ($diagnosticCode -ne 0) { Write-TraceLog 'Firmware diagnostics failed; the ETL is already saved.' }
Write-TraceLog "FINISHED. Recording files: $base"
