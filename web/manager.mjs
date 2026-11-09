import { UUID, DEVICE_OPTIONS, BluetoothChannel, ConfigClient, ProtocolError } from "./protocol.mjs?v=1.2";

// Recursive timeout avoids overlapping refreshes. Stop invalidates an in-flight
// callback's generation, so it cannot resurrect polling after disconnect/save.
export class DiagnosticPoller {
  constructor(refresh, canRefresh, { interval = 1000, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    Object.assign(this, { refresh, canRefresh, interval, setTimer, clearTimer });
    this.timer = null; this.generation = 0;
  }
  start() {
    this.stop();
    const generation = this.generation;
    const schedule = () => {
      if (generation !== this.generation) return;
      this.timer = this.setTimer(async () => {
        this.timer = null;
        try { if (this.canRefresh()) await this.refresh(); }
        finally { schedule(); }
      }, this.interval);
    };
    schedule();
  }
  stop() { this.generation++; if (this.timer !== null) this.clearTimer(this.timer); this.timer = null; }
}

// null means unavailable, not an empty OS pairing list. This API only returns
// this origin's grants; callers must retain page-local selections without it.
export async function getAuthorizedDevices(bluetooth) {
  if (typeof bluetooth?.getDevices !== "function") return null;
  try { return await bluetooth.getDevices(); }
  catch (error) { throw connectionError(error, "authorized"); }
}

function connectionError(error, stage) {
  if (error?.code && error.name === "ProtocolError") return error;
  let code;
  if (["SecurityError", "NotAllowedError"].includes(error?.name)) code = "bluetoothPermission";
  else if (stage === "chooser" && error?.name === "NotFoundError") code = "chooserEmpty";
  else if (stage === "service" && error?.name === "NotFoundError") code = "serviceMissing";
  else if (stage === "characteristic" && error?.name === "NotFoundError") code = "characteristic";
  else code = "connectFailed";
  return new ProtocolError(code, { cause: error, stage });
}

export async function connectBluetooth(bluetooth, onState, onDisconnect, { device, onSelected } = {}) {
  if (!device) {
    if (!bluetooth?.requestDevice) throw new ProtocolError("unsupported");
    // No awaited discovery/permission check before requestDevice: keep the
    // user's click activation. Known-device failures never open a chooser.
    try { device = await bluetooth.requestDevice(DEVICE_OPTIONS); }
    catch (error) { throw connectionError(error, "chooser"); }
  }
  onSelected?.(device); // Keep the grant even if this connection fails.
  if (!device.gatt) throw new ProtocolError("gattUnavailable");
  let channel, closed = false, stage = "connect";
  const close = () => {
    if (closed) return;
    closed = true;
    device.removeEventListener("gattserverdisconnected", disconnected);
    if (channel) channel.close();
    else { try { device.gatt.disconnect(); } catch { /* Already disconnected. */ } }
  };
  const disconnected = () => {
    if (closed) return;
    close();
    if (channel) onDisconnect?.();
  };
  const check = () => { if (closed || !device.gatt.connected) throw new ProtocolError("disconnected"); };
  device.addEventListener("gattserverdisconnected", disconnected);
  try {
    const server = await device.gatt.connect();
    check(); stage = "service";
    const service = await server.getPrimaryService(UUID.service);
    check(); stage = "characteristic";
    const request = await service.getCharacteristic(UUID.request);
    check();
    const response = await service.getCharacteristic(UUID.response);
    check();
    if (typeof request.writeValueWithResponse !== "function" || typeof response.readValue !== "function") throw new ProtocolError("characteristic");
    channel = new BluetoothChannel(device, request, response);
    return { device, channel, client: new ConfigClient(channel, onState), close };
  } catch (error) {
    close();
    throw connectionError(error, stage);
  }
}
