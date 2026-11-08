export const UUID = Object.freeze({
  service: "85300001-2015-4c53-8f44-58426c65434d",
  request: "85300002-2015-4c53-8f44-58426c65434d",
  response: "85300003-2015-4c53-8f44-58426c65434d",
});
export const DEVICE_OPTIONS = Object.freeze({ filters: [{ name: "Xbox Wireless Controller" }], optionalServices: [UUID.service] });
export const OP = Object.freeze({ READ: 1, STAGE: 2, COMMIT: 3, DIAG: 0x10 });
export const STATUS = Object.freeze({ OK: 0, INVALID: 1, CONFLICT: 2, BUSY: 3, PENDING: 4, TIMEOUT: 5, NOT_LOADED: 6, STORAGE_ERROR: 7 });
export const ANGLES = Object.freeze([45, 5, 10, 15, 20, 25, 30, 35, 40, 0, 50, 55, 60, 65, 70, 75]);
export const hex = (bytes) => Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join(" ");
const viewOf = (bytes) => new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
const equal = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

export class ProtocolError extends Error {
  constructor(code, detail = {}) { super(code); this.name = "ProtocolError"; this.code = code; Object.assign(this, detail); }
}
export class StatusError extends ProtocolError {
  constructor(reply) {
    super(`status${reply.status}`, { status: reply.status, opcode: reply.opcode, arg: reply.arg,
      hostStatus: viewOf(reply.payload).getUint32(4, true) });
    this.name = "StatusError";
  }
}
export function bytesOf(data, length) {
  const b = data instanceof ArrayBuffer ? new Uint8Array(data) : ArrayBuffer.isView(data) ?
    new Uint8Array(data.buffer, data.byteOffset, data.byteLength) : null;
  if (!b || b.length !== length) throw new ProtocolError("frame", { length: b?.length });
  return b.slice();
}
export function requestBytes(opcode, seq, arg, payload = new Uint8Array(16)) {
  if (![opcode, seq, arg].every((v) => Number.isInteger(v) && v >= 0 && v <= 255)) throw new ProtocolError("frame");
  const result = new Uint8Array(20);
  result.set([opcode, seq, arg, 0]); result.set(bytesOf(payload, 16), 4);
  return result;
}
export function parseResponse(value) {
  const b = bytesOf(value, 20);
  return { opcode: b[0], seq: b[1], status: b[2], arg: b[3], payload: b.slice(4) };
}
export function crc32(bytes) {
  let crc = 0xffffffff;
  for (const byte of bytes) {
    crc ^= byte;
    for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ (crc & 1 ? 0xedb88320 : 0);
  }
  return (crc ^ 0xffffffff) >>> 0;
}
export function defaultMapping() {
  return { bindings: [1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 10, 10, 0, 11, 16, 17],
    stickDeadzone: 3000, padDeadzone: 3500, dpadThreshold: 8000,
    invertRightX: false, invertRightY: false, requireLeftPadClick: true, diagonalAngle: 30 };
}
export function encodeMapping(m) {
  if (!Array.isArray(m?.bindings) || m.bindings.length !== 16 ||
      !m.bindings.every((v) => Number.isInteger(v) && v >= 0 && v <= 17) ||
      ![m.stickDeadzone, m.padDeadzone].every((v) => Number.isInteger(v) && v >= 0 && v <= 16000) ||
      !Number.isInteger(m.dpadThreshold) || m.dpadThreshold < 256 || m.dpadThreshold > 30000 ||
      ![m.invertRightX, m.invertRightY, m.requireLeftPadClick].every((v) => typeof v === "boolean") ||
      !ANGLES.includes(m.diagonalAngle)) throw new ProtocolError("mapping");
  const b = new Uint8Array(24), v = viewOf(b);
  b.set([1, Number(m.invertRightX) | (Number(m.invertRightY) << 1) | (Number(m.requireLeftPadClick) << 2) |
    (ANGLES.indexOf(m.diagonalAngle) << 4), ...m.bindings]);
  v.setUint16(18, m.stickDeadzone, true); v.setUint16(20, m.padDeadzone, true); v.setUint16(22, m.dpadThreshold, true);
  return b;
}
export function decodeMapping(data) {
  const b = bytesOf(data, 24), v = viewOf(b);
  if (b[0] !== 1 || (b[1] & 8)) throw new ProtocolError("mapping");
  const m = { bindings: Array.from(b.slice(2, 18)), stickDeadzone: v.getUint16(18, true),
    padDeadzone: v.getUint16(20, true), dpadThreshold: v.getUint16(22, true),
    invertRightX: Boolean(b[1] & 1), invertRightY: Boolean(b[1] & 2),
    requireLeftPadClick: Boolean(b[1] & 4), diagonalAngle: ANGLES[b[1] >>> 4] };
  encodeMapping(m);
  return m;
}

// One queue owns ALL GATT operations, including complete two-page reads and saves.
// A timed-out browser promise cannot be cancelled. Close the channel before
// releasing its queue, so it can never overlap later I/O on this connection.
export class BluetoothChannel {
  #tail = Promise.resolve();
  #abort = new AbortController();
  #seq;
  constructor(device, request, response, { readTimeout = 5000, saveTimeout = 12000, pollInterval = 50,
    initialSequence = globalThis.crypto?.getRandomValues(new Uint8Array(1))[0] ?? 0 } = {}) {
    Object.assign(this, { device, request, response, readTimeout, saveTimeout, pollInterval });
    this.#seq = initialSequence & 255;
    this.disconnected = () => this.close(new ProtocolError("disconnected"));
    device.addEventListener("gattserverdisconnected", this.disconnected);
  }
  get closed() { return this.#abort.signal.aborted; }
  close(reason = new ProtocolError("disconnected")) {
    if (this.closed) return;
    this.#abort.abort(reason);
    this.device.removeEventListener("gattserverdisconnected", this.disconnected);
    try { this.device.gatt.disconnect(); } catch { /* Already disconnected. */ }
  }
  #check() {
    if (this.closed) throw this.#abort.signal.reason;
    if (!this.device.gatt.connected) { this.close(); throw this.#abort.signal.reason; }
  }
  async #bounded(operation, deadline) {
    this.#check();
    if (Date.now() >= deadline) { this.close(new ProtocolError("timeout")); throw this.#abort.signal.reason; }
    let timer, listener;
    try {
      return await Promise.race([
        Promise.resolve().then(() => { this.#check(); return operation(); }),
        new Promise((_, reject) => {
          listener = () => reject(this.#abort.signal.reason);
          this.#abort.signal.addEventListener("abort", listener, { once: true });
          timer = setTimeout(() => this.close(new ProtocolError("timeout")), Math.max(0, deadline - Date.now()));
        }),
      ]);
    } finally {
      clearTimeout(timer);
      this.#abort.signal.removeEventListener("abort", listener);
    }
  }
  async #pause(deadline) {
    let timer;
    try { await this.#bounded(() => new Promise((resolve) => { timer = setTimeout(resolve, this.pollInterval); }), deadline); }
    finally { clearTimeout(timer); }
  }
  transaction(operation) {
    const run = this.#tail.then(() => {
      this.#check();
      return operation((...args) => this.#exchange(...args));
    });
    this.#tail = run.catch(() => {});
    return run;
  }
  exchange(...args) { return this.transaction((exchange) => exchange(...args)); }
  async #exchange(opcode, arg = 0, payload = new Uint8Array(16), options = {}) {
    this.#check();
    const deadline = options.deadline ?? Date.now() + (opcode === OP.STAGE || opcode === OP.COMMIT ? this.saveTimeout : this.readTimeout);
    if (Date.now() >= deadline) { this.close(new ProtocolError("timeout")); throw this.#abort.signal.reason; }
    this.#seq = (this.#seq + 1) & 255;
    const seq = this.#seq, request = requestBytes(opcode, seq, arg, payload);
    // Never writeValue / writeValueWithoutResponse; never retry a write, even commit.
    await this.#bounded(() => this.request.writeValueWithResponse(request), deadline);
    for (;;) {
      const reply = parseResponse(await this.#bounded(() => this.response.readValue(), deadline));
      if (reply.opcode === opcode && reply.seq === seq && reply.arg === arg && reply.status !== STATUS.PENDING) {
        if (reply.status > STATUS.STORAGE_ERROR) throw new ProtocolError("statusUnknown", { status: reply.status });
        if (reply.status !== STATUS.OK) throw new StatusError(reply);
        return reply;
      }
      await this.#pause(deadline);
    }
  }
}

async function readSnapshot(exchange, attempts = 3) {
  for (let attempt = 0; attempt < attempts; attempt++) {
    const first = await exchange(OP.READ, 0), second = await exchange(OP.READ, 1);
    const wire = new Uint8Array([...first.payload, ...second.payload.slice(0, 8)]);
    const v = viewOf(second.payload), token = v.getUint32(8, true);
    if (crc32(wire) !== token) continue; // Only these read-only requests may repeat.
    return { mapping: decodeMapping(wire), wire, token, hostStatus: v.getUint32(12, true) };
  }
  throw new ProtocolError("crc");
}

export class ConfigClient {
  constructor(channel, onState = () => {}) {
    this.channel = channel; this.onState = onState; this.snapshot = null; this.state = "idle";
  }
  #state(state, detail = {}) { this.state = state; this.onState({ state, ...detail }); }
  readMapping() {
    return this.channel.transaction(async (exchange) => {
      this.snapshot = null;
      const result = await readSnapshot(exchange);
      this.snapshot = result;
      return result;
    });
  }
  saveMapping(mapping) {
    const wire = encodeMapping(mapping), previous = this.snapshot;
    if (!previous) return Promise.reject(new ProtocolError("readFirst"));
    return this.channel.transaction(async (exchange) => {
      // A queued save must not silently rebase itself onto a newer read/save.
      if (this.snapshot !== previous) throw new ProtocolError("readFirst");
      let persisted = false, commitStarted = false;
      this.snapshot = null;
      this.#state("staging");
      try {
        const deadline = Date.now() + this.channel.saveTimeout;
        const first = new Uint8Array(16), second = new Uint8Array(16);
        viewOf(first).setUint32(0, previous.token, true); first.set(wire.slice(0, 12), 4);
        second.set(wire.slice(12));
        await exchange(OP.STAGE, 0, first, { deadline });
        await exchange(OP.STAGE, 1, second, { deadline });
        this.#state("saving");
        commitStarted = true;
        const commit = new Uint8Array(16);
        viewOf(commit).setUint32(0, crc32(wire), true);
        viewOf(commit).setUint32(4, previous.token, true);
        const ack = await exchange(OP.COMMIT, 0, commit, { deadline });
        const v = viewOf(ack.payload), token = v.getUint32(0, true), hostStatus = v.getUint32(4, true);
        if (token !== crc32(wire) || hostStatus !== 0 || ack.payload.slice(8).some(Boolean)) throw new ProtocolError("commitAck");
        persisted = true;
        this.#state("persisted", { token, hostStatus });
        const result = await readSnapshot(exchange);
        if (!equal(result.wire, wire)) throw new ProtocolError("readback");
        this.snapshot = result;
        this.#state("verified", { token: result.token });
        return result;
      } catch (error) {
        const state = persisted ? "persistedUnverified" : error.status === STATUS.CONFLICT ? "conflict" :
          error instanceof StatusError && error.status !== STATUS.TIMEOUT ? "failed" : "unknown";
        Object.assign(error, { saveState: state, persisted, commitStarted, needsRead: true });
        this.#state(state, { error });
        throw error;
      }
    });
  }
  readDiagnostics() {
    return this.channel.transaction(async (exchange) => {
      const pages = [];
      const startedAt = new Date().toISOString();
      for (let arg = 0; arg < 6; arg++) pages.push((await exchange(OP.DIAG, arg)).payload);
      return { ...parseDiagnostics(pages), startedAt, capturedAt: new Date().toISOString() };
    });
  }
}

export function parseXboxReport(data) {
  const b = bytesOf(data, 16), v = viewOf(b);
  return { lx: v.getUint16(0, true), ly: v.getUint16(2, true), rx: v.getUint16(4, true), ry: v.getUint16(6, true),
    lt: v.getUint16(8, true), rt: v.getUint16(10, true), hat: b[12] & 15,
    buttons: v.getUint16(13, true), share: Boolean(b[15] & 1), hex: hex(b) };
}
export function parseDiagnostics(data) {
  if (!Array.isArray(data) || data.length !== 6) throw new ProtocolError("diagnostic");
  const pages = data.map((p) => bytesOf(p, 16)), views = pages.map(viewOf);
  const dwords = (page, names) => Object.fromEntries(names.map((name, i) => [name, views[page].getUint32(4 * i, true)]));
  const identity = views[0].getUint32(0, true), firmwareRev = views[0].getUint16(4, true), protocol = pages[0][6];
  if (identity !== 0x32435358 || firmwareRev < 2 || protocol !== 1) throw new ProtocolError("identity");
  const flags = pages[0][7], profileFlags = pages[3][11];
  return {
    identity: { magic: "XSC2", firmwareRev, protocol, configFlags: flags, loaded: Boolean(flags & 1),
      pending: Boolean(flags & 2), staged: Boolean(flags & 4), ticks: views[0].getUint32(8, true), hostStatus: views[0].getUint32(12, true) },
    input: { ...dwords(1, ["rx", "parseOK", "parseBad"]), lastLength: views[1].getUint16(12, true), received: views[1].getUint16(14, true) },
    hvx: { ...dwords(2, ["attempts", "success", "lastResult"]), conn: views[2].getUint16(12, true), securityModeLevel: pages[2][14], keySize: pages[2][15] },
    link: { ...dwords(3, ["secError", "cccdError"]), cccd: views[3].getUint16(8, true), blockReason: pages[3][10], profileFlags,
      profile: Boolean(profileFlags & 1), initialized: Boolean(profileFlags & 2), suspended: Boolean(profileFlags & 4),
      bootProtocol: Boolean(profileFlags & 8), serviceCalls: views[3].getUint32(12, true) },
    raw: { ...dwords(4, ["buttons", "suppress"]), lastHeader: views[4].getUint16(8, true) },
    report: parseXboxReport(pages[5]),
  };
}
