import { UUID, DEVICE_OPTIONS, BluetoothChannel, ConfigClient, ProtocolError } from "./protocol.mjs";

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

export async function connectBluetooth(bluetooth, onState, onDisconnect) {
  if (!bluetooth?.requestDevice) throw new ProtocolError("unsupported");
  // Keep requestDevice directly in the user's click activation; service UUID is
  // not advertised and MUST NOT be placed in filters.services.
  const device = await bluetooth.requestDevice(DEVICE_OPTIONS);
  let channel;
  const disconnected = () => { channel?.close(); onDisconnect?.(); };
  device.addEventListener("gattserverdisconnected", disconnected);
  try {
    const server = await device.gatt.connect();
    const service = await server.getPrimaryService(UUID.service);
    const request = await service.getCharacteristic(UUID.request);
    const response = await service.getCharacteristic(UUID.response);
    if (typeof request.writeValueWithResponse !== "function" || typeof response.readValue !== "function") throw new ProtocolError("characteristic");
    channel = new BluetoothChannel(device, request, response);
    return { device, channel, client: new ConfigClient(channel, onState), close() {
      device.removeEventListener("gattserverdisconnected", disconnected); channel.close();
    } };
  } catch (error) {
    device.removeEventListener("gattserverdisconnected", disconnected);
    device.gatt?.disconnect();
    throw error;
  }
}
