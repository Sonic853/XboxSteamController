param(
    [string]$Address,
    [ValidateRange(1,60)][int]$Samples = 2,
    [string]$OutputPath = (Join-Path (Get-Location) ("xbox-diagnostics-" + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json'))
)
$ErrorActionPreference = 'Stop'
if (-not $Address) {
    $devices = @(Get-PnpDevice -PresentOnly | Where-Object {
        $_.FriendlyName -eq 'Xbox Wireless Controller' -and $_.InstanceId -match '^BTHLE\\DEV_([0-9A-F]{12})\\'
    })
    if ($devices.Count -ne 1) { throw 'Specify -Address with the Bluetooth address of the target controller.' }
    $Address = ($devices[0].InstanceId -split '\\')[1].Substring(4)
}
Add-Type -AssemblyName System.Runtime.WindowsRuntime
# PowerShell 5.1 can expose a WinRT IBuffer as a raw COM object. Read its documented
# IBuffer/IBufferByteAccess interfaces without changing device access permissions.
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
[ComImport, Guid("905a0fe0-bc53-11df-8c49-001e4fc686da"), InterfaceType(ComInterfaceType.InterfaceIsIInspectable)]
interface BufferInfo { uint Capacity { get; } uint Length { get; set; } }
[ComImport, Guid("905a0fef-bc53-11df-8c49-001e4fc686da"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface BufferBytes { void Buffer(out IntPtr value); }
public static class GattBufferReader {
    public static byte[] Read(object buffer) {
        uint length = ((BufferInfo)buffer).Length;
        if (length != 20) throw new Exception("Invalid response length: " + length);
        IntPtr pointer;
        ((BufferBytes)buffer).Buffer(out pointer);
        byte[] result = new byte[length];
        Marshal.Copy(pointer, result, 0, result.Length);
        return result;
    }
}
'@
[Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.BluetoothCacheMode, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
[Windows.Devices.Bluetooth.GenericAttributeProfile.GattReadResult, Windows.Devices.Bluetooth, ContentType=WindowsRuntime] | Out-Null
$asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and
    $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
} | Select-Object -First 1
function Await-WinRT($Operation, [Type]$ResultType) {
    $task = $asTask.MakeGenericMethod($ResultType).Invoke($null, @($Operation))
    if (-not $task.Wait(15000)) { throw 'Bluetooth operation timed out after 15 seconds.' }
    return $task.Result
}
function Read-DiagnosticPage([byte]$Sequence, [byte]$Page) {
    # Diagnostic opcode only: never stage/commit mappings, pair or flash firmware.
    $wire = New-Object byte[] 20
    $wire[0] = 0x10
    $wire[1] = $Sequence
    $wire[2] = $Page
    $buffer = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeBufferExtensions]::AsBuffer($wire)
    $status = Await-WinRT ($request.WriteValueAsync($buffer, [Windows.Devices.Bluetooth.GenericAttributeProfile.GattWriteOption]::WriteWithResponse)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCommunicationStatus])
    if ($status.ToString() -ne 'Success') { throw "Diagnostic request failed: $status" }
    $deadline = [DateTime]::UtcNow.AddSeconds(5)
    do {
        $reply = Await-WinRT ($response.ReadValueAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattReadResult])
        if ($reply.Status.ToString() -ne 'Success') { throw "Diagnostic read failed: $($reply.Status), ATT=$($reply.ProtocolError)" }
        $bytes = [GattBufferReader]::Read($reply.Value)
        if ($bytes[0] -eq 0x10 -and $bytes[1] -eq $Sequence -and $bytes[3] -eq $Page) {
            if ($bytes[2] -eq 0) { return ,([byte[]]$bytes[4..19]) }
            if ($bytes[2] -ne 4) { throw "Diagnostic status: $($bytes[2])" }
        }
        Start-Sleep -Milliseconds 50
    } while ([DateTime]::UtcNow -lt $deadline)
    throw "Diagnostic page $Page timeout; last response: $([BitConverter]::ToString($bytes))"
}
$device = Await-WinRT ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync([Convert]::ToUInt64(($Address -replace '[:-]', ''),16))) ([Windows.Devices.Bluetooth.BluetoothLEDevice])
if ($null -eq $device) { throw 'Windows could not open the controller.' }
$services = @()
try {
    $result = Await-WinRT ($device.GetGattServicesForUuidAsync([guid]'85300001-2015-4c53-8f44-58426c65434d', [Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattDeviceServicesResult])
    if ($result.Status.ToString() -ne 'Success' -or $result.Services.Count -ne 1) { throw "Configuration service discovery failed: $($result.Status)" }
    $services = @($result.Services)
    $chars = Await-WinRT ($services[0].GetCharacteristicsAsync([Windows.Devices.Bluetooth.BluetoothCacheMode]::Uncached)) ([Windows.Devices.Bluetooth.GenericAttributeProfile.GattCharacteristicsResult])
    if ($chars.Status.ToString() -ne 'Success') { throw "Characteristic discovery failed: $($chars.Status)" }
    $request = $chars.Characteristics | Where-Object { $_.Uuid -eq [guid]'85300002-2015-4c53-8f44-58426c65434d' }
    $response = $chars.Characteristics | Where-Object { $_.Uuid -eq [guid]'85300003-2015-4c53-8f44-58426c65434d' }
    if (-not $request -or -not $response) { throw 'Configuration characteristics are missing.' }
    $sequence = Get-Random -Minimum 1 -Maximum 256
    $records = @()
    for ($sample = 0; $sample -lt $Samples; $sample++) {
        $pages = @()
        $limit = 6
        for ($page = 0; $page -lt $limit; $page++) {
            $sequence = ($sequence + 1) -band 255
            $data = Read-DiagnosticPage $sequence $page
            if ($page -eq 0) {
                if ([BitConverter]::ToUInt32($data, 0) -ne 0x32435358 -or $data[6] -ne 1 -or [BitConverter]::ToUInt16($data, 4) -lt 2) {
                    throw 'Unexpected diagnostic identity or protocol.'
                }
                if ([BitConverter]::ToUInt16($data, 4) -ge 3) { $limit = 8 }
                if ([BitConverter]::ToUInt16($data, 4) -ge 4) { $limit = 9 }
            }
            $pages += [BitConverter]::ToString($data).Replace('-', '')
        }
        $records += [pscustomobject]@{ capturedAt=[DateTime]::UtcNow.ToString('o'); pages=$pages }
        if ($sample + 1 -lt $Samples) { Start-Sleep -Milliseconds 500 }
    }
    $json = [pscustomobject]@{ format='xsc-native-ble-diagnostics'; device=$device.Name; connected=$device.ConnectionStatus.ToString(); samples=$records } | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText($OutputPath, $json, (New-Object Text.UTF8Encoding $false))
    $json
    Write-Host "Saved: $OutputPath"
} finally {
    foreach ($service in $services) { $service.Dispose() }
    $device.Dispose()
}
