import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { BluetoothChannel, ConfigClient, UUID, DEVICE_OPTIONS, OP, STATUS, ProtocolError, StatusError,
  crc32, requestBytes, parseResponse, defaultMapping, encodeMapping, decodeMapping, parseDiagnostics } from "../web/protocol.mjs";
import { DiagnosticPoller, connectBluetooth } from "../web/manager.mjs";
import { messages, sources, targets, blockReasons, setLanguage, getLanguage, initialLanguage, t } from "../web/i18n.mjs";

// Fixture bytes are independent of encodeMapping. Mock firmware speaks the real
// 20-byte wire format; it does not replace the client with a fake success API.
const DEFAULT = Uint8Array.from([1, 0x64, 1, 2, 3, 4, 5, 6, 7, 8, 9, 11, 10, 10, 0, 11, 16, 17, 0xb8, 0x0b, 0xac, 0x0d, 0x40, 0x1f]);
const dv = (b) => new DataView(b.buffer, b.byteOffset, b.byteLength);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const deferred = () => { let resolve; const promise = new Promise((r) => { resolve = r; }); return { promise, resolve }; };
// Table-based reference implementation used only by the mock device.
const table = Array.from({ length: 256 }, (_, n) => {
  for (let bit = 0; bit < 8; bit++) n = n & 1 ? 0xedb88320 ^ (n >>> 1) : n >>> 1;
  return n >>> 0;
});
const token = (bytes) => (bytes.reduce((c, b) => table[(c ^ b) & 255] ^ (c >>> 8), 0xffffffff) ^ 0xffffffff) >>> 0;
function response(request, status = 0, payload = new Uint8Array(16)) {
  const b = new Uint8Array(20); b.set([request[0], request[1], status, request[2]]); b.set(payload, 4); return b;
}
function diagPages() {
  const pages = Array.from({ length: 6 }, () => new Uint8Array(16));
  dv(pages[0]).setUint32(0, 0x32435358, true); dv(pages[0]).setUint16(4, 2, true);
  pages[0][6] = 1; pages[0][7] = 7; dv(pages[0]).setUint32(8, 0xf1234567, true); dv(pages[0]).setUint32(12, 42, true);
  [101, 99, 2].forEach((n, i) => dv(pages[1]).setUint32(i * 4, n, true));
  dv(pages[1]).setUint16(12, 23, true); dv(pages[1]).setUint16(14, 0x3b0, true);
  [90, 87, 8].forEach((n, i) => dv(pages[2]).setUint32(i * 4, n, true));
  dv(pages[2]).setUint16(12, 0x1234, true); pages[2][14] = 0x21; pages[2][15] = 16;
  dv(pages[3]).setUint32(0, 5, true); dv(pages[3]).setUint32(4, 6, true); dv(pages[3]).setUint16(8, 1, true);
  pages[3][10] = 7; pages[3][11] = 11; dv(pages[3]).setUint32(12, 51, true);
  dv(pages[4]).setUint32(0, 0x108084, true); dv(pages[4]).setUint32(4, 0x2014, true); dv(pages[4]).setUint16(8, 0x3b4, true);
  [0, 65535, 32768, 12345, 1023, 512].forEach((n, i) => dv(pages[5]).setUint16(i * 2, n, true));
  pages[5][12] = 8; dv(pages[5]).setUint16(13, 0x5089, true); pages[5][15] = 1;
  return pages;
}
class MockDevice extends EventTarget {
  constructor(options = {}) {
    super(); Object.assign(this, options);
    this.mapping = DEFAULT.slice(); this.writes = []; this.reads = 0; this.pending = 0; this.inFlight = 0; this.maxInFlight = 0;
    this.gatt = { connected: true, disconnect: () => {
      if (!this.gatt.connected) return;
      this.gatt.connected = false; this.dispatchEvent(new Event("gattserverdisconnected"));
    } };
    this.request = { writeValueWithResponse: async (wire) => {
      this.inFlight++; this.maxInFlight = Math.max(this.maxInFlight, this.inFlight);
      try {
        const req = new Uint8Array(wire); this.writes.push(req);
        assert.equal(req.length, 20); assert.equal(req[3], 0);
        const old = this.reply;
        if (this.beforeWrite) await this.beforeWrite(req);
        if (this.rejectWrite?.(req)) throw new Error("simulated GATT write rejection after device may have received it");
        this.reply = this.handle(req);
        this.stale = this.staleFirst && old ? old : null;
        this.current = req;
      } finally { this.inFlight--; }
    } };
    this.response = { readValue: async () => {
      this.inFlight++; this.maxInFlight = Math.max(this.maxInFlight, this.inFlight); this.reads++;
      try {
        if (this.beforeRead) await this.beforeRead(this.current);
        let b;
        if (this.overrideRead) b = this.overrideRead(this.current);
        if (!b && this.stale) { b = this.stale; this.stale = null; }
        if (!b && this.current[0] === OP.COMMIT && this.pending > 0) {
          this.pending--; b = response(this.current, STATUS.PENDING);
        }
        b ??= this.reply;
        // Nonzero DataView offset catches accidental buffer-wide decoding.
        const backing = new Uint8Array(b.length + 9); backing.set(b, 5);
        return new DataView(backing.buffer, 5, b.length);
      } finally { this.inFlight--; }
    } };
  }
  handle(req) {
    const [op, , arg] = req, payload = new Uint8Array(16);
    const forced = this.statusFor?.(req);
    if (forced !== undefined) { dv(payload).setUint32(4, 0xdeadbeef, true); return response(req, forced, payload); }
    if (op === OP.READ) {
      if (arg === 0) {
        payload.set(this.mapping.slice(0, 16));
        this.afterPage0?.();
      } else {
        payload.set(this.mapping.slice(16)); dv(payload).setUint32(8, token(this.mapping), true); dv(payload).setUint32(12, 9, true);
        if (this.corruptCRC) payload[8] ^= 1;
      }
    } else if (op === OP.STAGE) {
      if (arg === 0) {
        if (dv(req).getUint32(4, true) !== token(this.mapping)) return response(req, STATUS.CONFLICT);
        this.expectedToken = dv(req).getUint32(4, true);
        this.staged = new Uint8Array(24); this.staged.set(req.slice(8));
      } else {
        assert.deepEqual(req.slice(16), new Uint8Array(4));
        this.staged.set(req.slice(4, 16), 12);
      }
    } else if (op === OP.COMMIT) {
      assert.deepEqual(req.slice(12), new Uint8Array(8));
      this.beforeCommit?.();
      if (dv(req).getUint32(4, true) !== token(this.staged) || dv(req).getUint32(8, true) !== this.expectedToken ||
          this.expectedToken !== token(this.mapping)) return response(req, STATUS.CONFLICT);
      this.mapping = this.staged.slice();
      dv(payload).setUint32(0, token(this.mapping), true); dv(payload).setUint32(4, 0, true);
      this.afterCommit?.(payload);
    } else if (op === OP.DIAG) payload.set(diagPages()[arg]);
    return response(req, STATUS.OK, payload);
  }
}
function setup(t, options = {}, timeouts = {}) {
  const device = new MockDevice(options), states = [];
  const channel = new BluetoothChannel(device, device.request, device.response, { pollInterval: 1, initialSequence: 10, ...timeouts });
  const client = new ConfigClient(channel, (event) => states.push(event));
  t.after(() => channel.close());
  return { device, channel, client, states };
}

test("CRC standard vector, exact mapping fixture and complete format validation", () => {
  assert.equal(crc32(new TextEncoder().encode("123456789")), 0xcbf43926);
  assert.equal(crc32(DEFAULT), token(DEFAULT));
  assert.deepEqual(encodeMapping(defaultMapping()), DEFAULT);
  assert.deepEqual(decodeMapping(DEFAULT), defaultMapping());
  assert.equal(defaultMapping().bindings[9], 11); assert.equal(defaultMapping().bindings[10], 10);
  for (const angle of [45, 5, 10, 15, 20, 25, 30, 35, 40, 0, 50, 55, 60, 65, 70, 75]) {
    assert.equal(decodeMapping(encodeMapping({ ...defaultMapping(), diagonalAngle: angle })).diagonalAngle, angle);
  }
  for (const [index, value] of [[0, 2], [1, 0x6c], [2, 18], [19, 255], [23, 255]]) {
    const b = DEFAULT.slice(); b[index] = value; assert.throws(() => decodeMapping(b), { code: "mapping" });
  }
  for (const override of [{ stickDeadzone: -1 }, { padDeadzone: 16001 }, { dpadThreshold: 255 }, { dpadThreshold: 30001 },
    { stickDeadzone: 1.5 }, { padDeadzone: NaN }, { diagonalAngle: 31 }, { invertRightX: 1 }]) {
    assert.throws(() => encodeMapping({ ...defaultMapping(), ...override }), { code: "mapping" });
  }
});

test("fixed 20-byte frames reject malformed length and preserve DataView offsets", () => {
  const request = requestBytes(1, 255, 1); assert.deepEqual(Array.from(request.slice(0, 4)), [1, 255, 1, 0]);
  assert.equal(request.length, 20);
  assert.throws(() => requestBytes(1, 1, 0, new Uint8Array(15)), ProtocolError);
  assert.throws(() => parseResponse(new Uint8Array(21)), ProtocolError);
  const b = new Uint8Array(24); b.set([3, 77, 4, 0], 2);
  assert.deepEqual(parseResponse(new DataView(b.buffer, 2, 20)), { opcode: 3, seq: 77, status: 4, arg: 0, payload: new Uint8Array(16) });
});

test("page reads validate all 24 bytes and ignore stale sequence/opcode/arg", async (t) => {
  const { device, client } = setup(t, { staleFirst: true });
  const seen = new Map();
  device.overrideRead = (req) => {
    const n = seen.get(req[1]) ?? 0; seen.set(req[1], n + 1);
    const b = device.reply.slice();
    if (n === 0) { b[1] ^= 0x80; return b; }
    if (n === 1) { b[0] = OP.DIAG; return b; }
    if (n === 2) { b[3] ^= 1; return b; }
  };
  const result = await client.readMapping();
  assert.deepEqual(result.wire, DEFAULT); assert.equal(result.hostStatus, 9);
  assert.deepEqual(device.writes.map((r) => [r[0], r[2]]), [[1, 0], [1, 1]]);
  assert.ok(device.reads >= 8); assert.equal(device.maxInFlight, 1);
});

test("torn pages retry only reads and converge on a CRC-consistent snapshot", async (t) => {
  const { device, client } = setup(t);
  device.afterPage0 = () => { device.mapping[2] = 17; device.afterPage0 = null; };
  const snapshot = await client.readMapping();
  assert.equal(snapshot.mapping.bindings[0], 17);
  assert.deepEqual(device.writes.map((r) => r[2]), [0, 1, 0, 1]);
  assert.ok(device.writes.every((r) => r[0] === OP.READ));
});

test("bad CRC never publishes a mapping; CRC-valid reserved bit is rejected", async (t) => {
  const { device, client } = setup(t, { corruptCRC: true });
  await assert.rejects(client.readMapping(), { code: "crc" });
  assert.equal(client.snapshot, null); assert.equal(device.writes.length, 6);
  device.corruptCRC = false; device.mapping[1] |= 8;
  await assert.rejects(client.readMapping(), { code: "mapping" });
  assert.equal(client.snapshot, null);
});

test("save stages 12+12 bytes, waits through pending, commits once and verifies readback", async (t) => {
  const { device, client, states } = setup(t, { pending: 3, staleFirst: true });
  device.pending = 3;
  const previous = await client.readMapping();
  const mapping = { ...defaultMapping(), stickDeadzone: 100, invertRightX: true, diagonalAngle: 75 };
  const result = await client.saveMapping(mapping);
  assert.deepEqual(result.mapping, mapping);
  const stage = device.writes.filter((r) => r[0] === OP.STAGE);
  assert.equal(dv(stage[0]).getUint32(4, true), previous.token);
  assert.deepEqual(stage[0].slice(8), encodeMapping(mapping).slice(0, 12));
  assert.deepEqual(stage[1].slice(4, 16), encodeMapping(mapping).slice(12));
  const commit = device.writes.find((r) => r[0] === OP.COMMIT);
  assert.equal(dv(commit).getUint32(4, true), token(encodeMapping(mapping)));
  assert.equal(dv(commit).getUint32(8, true), previous.token);
  assert.deepEqual(commit.slice(12), new Uint8Array(8));
  assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
  assert.deepEqual(states.map((s) => s.state), ["staging", "saving", "persisted", "verified"]);
  assert.equal(device.maxInFlight, 1);
});

test("optimistic token conflict stops before commit and requires a fresh read", async (t) => {
  const { device, client, states } = setup(t);
  await client.readMapping(); device.mapping[2] = 0;
  await assert.rejects(client.saveMapping(defaultMapping()), (error) => error.status === 2 && error.saveState === "conflict" && error.needsRead);
  assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 0);
  assert.equal(client.snapshot, null); assert.equal(states.at(-1).state, "conflict");
  await assert.rejects(client.saveMapping(defaultMapping()), { code: "readFirst" });
  await client.readMapping(); await client.saveMapping(defaultMapping());
});

test("interleaved staging cannot commit a mixed mapping or another client's expected token", async (t) => {
  for (const replaceExpected of [false, true]) {
    const { device, client, states } = setup(t);
    await client.readMapping();
    const intended = { ...defaultMapping(), padDeadzone: 1200 };
    device.beforeCommit = () => {
      // Simulate a second browser/process interleaving stage writes outside our
      // local transaction queue. The first client's commit guard must reject.
      if (replaceExpected) device.expectedToken = (device.expectedToken ^ 1) >>> 0;
      else device.staged[2] = 17;
    };
    await assert.rejects(client.saveMapping(intended), (e) => e.status === 2 && e.saveState === "conflict" && e.needsRead);
    assert.deepEqual(device.mapping, DEFAULT);
    assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
    assert.ok(!states.some((s) => s.state === "persisted" || s.state === "verified"));
  }
});

test("second-stage errors never send commit; status and storage payload remain available", async (t) => {
  for (const status of [STATUS.INVALID, STATUS.BUSY, STATUS.NOT_LOADED, STATUS.STORAGE_ERROR]) {
    const { device, client } = setup(t, { statusFor: (r) => r[0] === OP.STAGE && r[2] === 1 ? status : undefined });
    await client.readMapping();
    await assert.rejects(client.saveMapping(defaultMapping()), (e) => e instanceof StatusError && e.status === status && e.hostStatus === 0xdeadbeef && e.saveState === "failed" && e.needsRead);
    assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 0);
  }
});

test("device persistence timeout is unknown and commit is never automatically resent", async (t) => {
  const { device, client } = setup(t, { statusFor: (r) => r[0] === OP.COMMIT ? STATUS.TIMEOUT : undefined });
  await client.readMapping();
  await assert.rejects(client.saveMapping(defaultMapping()), (e) => e.saveState === "unknown" && e.status === 5 && !e.persisted && e.commitStarted);
  assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
  assert.equal(client.snapshot, null);
});

test("all commit rejection statuses preserve failure/conflict and force a fresh baseline", async (t) => {
  for (const status of [1, 2, 3, 6, 7]) {
    const { device, client } = setup(t, { statusFor: (r) => r[0] === OP.COMMIT ? status : undefined });
    await client.readMapping();
    await assert.rejects(client.saveMapping(defaultMapping()), (e) => e.saveState === (status === 2 ? "conflict" : "failed") && e.needsRead && !e.persisted);
    assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
    assert.equal(client.snapshot, null);
  }
});

test("local save deadline covers pending commit, closes channel and cancels queued work", async (t) => {
  const { device, channel, client, states } = setup(t, {}, { saveTimeout: 35 });
  await client.readMapping();
  device.overrideRead = (req) => req[0] === OP.COMMIT ? response(req, STATUS.PENDING) : null;
  const save = client.saveMapping(defaultMapping()), diag = client.readDiagnostics();
  const results = await Promise.allSettled([save, diag]);
  assert.equal(results[0].reason.code, "timeout"); assert.equal(results[0].reason.saveState, "unknown");
  assert.equal(results[1].status, "rejected"); assert.equal(channel.closed, true); assert.equal(device.gatt.connected, false);
  assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
  assert.equal(device.writes.filter((r) => r[0] === OP.DIAG).length, 0);
  assert.ok(!states.some((s) => s.state === "persisted" || s.state === "verified"));
});

test("hung browser write times out without overlapping or retrying it", async (t) => {
  const { device, channel, client } = setup(t, {}, { saveTimeout: 30 });
  await client.readMapping();
  const hung = deferred();
  device.beforeWrite = (req) => req[0] === OP.COMMIT ? hung.promise : undefined;
  const save = client.saveMapping(defaultMapping()), read = client.readMapping();
  const results = await Promise.allSettled([save, read]);
  assert.equal(results[0].reason.saveState, "unknown"); assert.equal(results[1].status, "rejected");
  assert.equal(channel.closed, true); assert.equal(device.maxInFlight, 1);
  assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
  hung.resolve(); await delay(0);
  assert.equal(client.snapshot, null);
});

test("hung read and stale-only reads respect the local read timeout", async (t) => {
  for (const hung of [true, false]) {
    const gate = deferred();
    const { device, channel, client } = setup(t, {}, { readTimeout: 25 });
    if (hung) device.beforeRead = () => gate.promise;
    else device.overrideRead = (req) => { const b = device.reply.slice(); b[1] = (req[1] - 1) & 255; return b; };
    await assert.rejects(client.readMapping(), { code: "timeout" });
    assert.equal(channel.closed, true); assert.equal(device.writes.length, 1); assert.equal(client.snapshot, null);
    gate.resolve(); await delay(0);
  }
});

test("write rejection after a possible device commit must remain unknown", async (t) => {
  const { device, client } = setup(t);
  await client.readMapping();
  device.rejectWrite = (req) => {
    if (req[0] !== OP.COMMIT) return false;
    device.mapping = device.staged.slice(); return true;
  };
  await assert.rejects(client.saveMapping({ ...defaultMapping(), padDeadzone: 555 }), (e) => e.saveState === "unknown" && e.needsRead);
  assert.equal(device.writes.filter((r) => r[0] === OP.COMMIT).length, 1);
  const result = await client.readMapping(); assert.equal(result.mapping.padDeadzone, 555);
});

test("bad ACK cannot claim persistence; successful ACK with changed readback is distinct", async (t) => {
  const bad = setup(t, { afterCommit: (payload) => { payload[8] = 1; } });
  await bad.client.readMapping();
  await assert.rejects(bad.client.saveMapping(defaultMapping()), (e) => e.code === "commitAck" && e.saveState === "unknown");
  const contradictory = setup(t, { afterCommit: (payload) => { dv(payload).setUint32(4, 0x10004, true); } });
  await contradictory.client.readMapping();
  await assert.rejects(contradictory.client.saveMapping(defaultMapping()), (e) => e.code === "commitAck" && e.saveState === "unknown");
  const changed = setup(t);
  await changed.client.readMapping();
  changed.device.afterCommit = () => { changed.device.mapping[2] = 17; };
  await assert.rejects(changed.client.saveMapping(defaultMapping()), (e) => e.code === "readback" && e.persisted && e.saveState === "persistedUnverified");
  assert.ok(changed.states.some((s) => s.state === "persisted"));
  assert.ok(!changed.states.some((s) => s.state === "verified"));
});

test("concurrent save/read/diagnostics keep entire logical transactions contiguous", async (t) => {
  const { device, client } = setup(t);
  await client.readMapping(); device.writes.length = 0;
  const save = client.saveMapping(defaultMapping());
  const secondSave = client.saveMapping(defaultMapping());
  const diag = client.readDiagnostics(); const read = client.readMapping();
  const results = await Promise.allSettled([save, secondSave, diag, read]);
  assert.equal(results[0].status, "fulfilled"); assert.equal(results[1].reason.code, "readFirst");
  assert.equal(results[2].status, "fulfilled"); assert.equal(results[3].status, "fulfilled");
  assert.deepEqual(device.writes.map((r) => [r[0], r[2]]), [[2, 0], [2, 1], [3, 0], [1, 0], [1, 1],
    [16, 0], [16, 1], [16, 2], [16, 3], [16, 4], [16, 5], [1, 0], [1, 1]]);
  assert.equal(device.maxInFlight, 1);
});

test("disconnect immediately aborts active save and queued transactions, even a hung read", async (t) => {
  const { device, channel, client } = setup(t);
  await client.readMapping(); const entered = deferred(), gate = deferred();
  device.beforeRead = (req) => { if (req[0] === OP.COMMIT) { entered.resolve(); return gate.promise; } };
  const save = client.saveMapping(defaultMapping()), read = client.readDiagnostics();
  const outcome = Promise.allSettled([save, read]);
  await entered.promise; device.gatt.disconnect();
  const results = await outcome;
  assert.equal(results[0].reason.code, "disconnected"); assert.equal(results[0].reason.saveState, "unknown");
  assert.equal(results[1].status, "rejected"); assert.equal(channel.closed, true);
  assert.equal(device.writes.filter((r) => r[0] === OP.DIAG).length, 0);
  gate.resolve(); await delay(0);
});

test("sequence wraps as an 8-bit value and pending with a matching header is never OK", async (t) => {
  const { channel, device } = setup(t, {}, { initialSequence: 254 });
  const seen = new Set();
  device.overrideRead = (req) => { if (!seen.has(req[1])) { seen.add(req[1]); return response(req, 4); } };
  await channel.exchange(OP.READ, 0); await channel.exchange(OP.READ, 1);
  assert.deepEqual(device.writes.map((r) => r[1]), [255, 0]); assert.equal(device.reads, 4);
});

test("six diagnostic pages decode LE fields, masks, axes, hat and report without ID", async (t) => {
  const { client } = setup(t); const result = await client.readDiagnostics();
  assert.equal(result.identity.magic, "XSC2"); assert.equal(result.identity.firmwareRev, 2); assert.equal(result.identity.ticks, 0xf1234567);
  assert.equal(result.identity.loaded, true); assert.equal(result.identity.pending, true); assert.equal(result.identity.staged, true);
  assert.deepEqual(result.input, { rx: 101, parseOK: 99, parseBad: 2, lastLength: 23, received: 0x3b0 });
  assert.equal(result.hvx.conn, 0x1234); assert.equal(result.hvx.securityModeLevel, 0x21); assert.equal(result.hvx.keySize, 16);
  assert.equal(result.link.blockReason, 7); assert.equal(result.link.serviceCalls, 51); assert.equal(result.link.bootProtocol, true);
  assert.equal(result.raw.buttons, 0x108084); assert.equal(result.raw.suppress, 0x2014); assert.equal(result.raw.lastHeader, 0x3b4);
  assert.deepEqual([result.report.lx, result.report.ly, result.report.rx, result.report.ry, result.report.lt, result.report.rt], [0, 65535, 32768, 12345, 1023, 512]);
  assert.equal(result.report.hat, 8); assert.equal(result.report.buttons, 0x5089); assert.equal(result.report.share, true);
  assert.equal(result.report.hex.split(" ").length, 16);
  assert.ok(result.startedAt); assert.ok(result.capturedAt);
  assert.doesNotMatch(JSON.stringify(result), /bond|secret|ltk|irk/i);
  for (const mutate of [(p) => p[0].fill(0, 0, 4), (p) => { p[0][4] = 1; }, (p) => { p[0][6] = 2; }]) {
    const pages = diagPages(); mutate(pages); assert.throws(() => parseDiagnostics(pages), { code: "identity" });
  }
});

test("device chooser uses exact name and optional, unadvertised service; GATT discovery is serial", async () => {
  const device = new MockDevice(), calls = [];
  device.gatt.connect = async () => ({ getPrimaryService: async (uuid) => {
    calls.push(uuid); return { getCharacteristic: async (id) => { calls.push(id); return id === UUID.request ? device.request : device.response; } };
  } });
  const bluetooth = { requestDevice: async (options) => {
    assert.deepEqual(options, { filters: [{ name: "Xbox Wireless Controller" }], optionalServices: [UUID.service] }); return device;
  } };
  const session = await connectBluetooth(bluetooth);
  assert.deepEqual(calls, [UUID.service, UUID.request, UUID.response]);
  assert.deepEqual(DEVICE_OPTIONS.filters, [{ name: "Xbox Wireless Controller" }]); session.close();
  assert.equal(device.gatt.connected, false);
});

test("polling skips busy work and stop prevents timer resurrection after in-flight refresh", async () => {
  const timers = new Map(); let nextID = 0, allowed = false, calls = 0;
  const gate = deferred();
  const poller = new DiagnosticPoller(async () => { calls++; await gate.promise; }, () => allowed, {
    setTimer: (fn, ms) => { assert.equal(ms, 1000); timers.set(++nextID, fn); return nextID; }, clearTimer: (id) => timers.delete(id),
  });
  const fire = () => { const [id, fn] = timers.entries().next().value; timers.delete(id); return fn(); };
  poller.start(); await fire(); assert.equal(calls, 0); assert.equal(timers.size, 1);
  allowed = true; const run = fire(); assert.equal(calls, 1); assert.equal(timers.size, 0);
  poller.stop(); gate.resolve(); await run; assert.equal(timers.size, 0);
  poller.start(); assert.equal(timers.size, 1); poller.stop(); assert.equal(timers.size, 0);
});

test("all UI localization keys exist in both languages; defaults action performs no I/O", async () => {
  const html = await readFile(new URL("../web/index.html", import.meta.url), "utf8");
  const app = await readFile(new URL("../web/app.mjs", import.meta.url), "utf8");
  for (const [, key] of html.matchAll(/data-i18n="([^"]+)"/g)) assert.ok(messages[key], key);
  for (const [key, values] of Object.entries(messages)) assert.ok(values.length === 2 && values.every((v) => typeof v === "string" && v.length), key);
  assert.equal(sources.en.length, 16); assert.equal(sources["zh-CN"].length, 16); assert.equal(targets.en.length, 18); assert.equal(blockReasons.en.length, 10);
  const handler = app.match(/\$\("defaults"\)\.addEventListener\("click", \(\) => \{([\s\S]*?)\n\}\);/)[1];
  assert.match(handler, /fillMapping\(defaultMapping\(\)\)/); assert.doesNotMatch(handler, /session|client|saveMapping|exchange/);
  assert.doesNotMatch(html + app, /navigator\.hid|WebHID|receiver-select|input-mode/);
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
  assert.equal(initialLanguage("zh-TW", blocked), "zh-CN"); assert.equal(initialLanguage("en-US", blocked), "en");
  setLanguage("en", blocked); assert.equal(t("conflict"), "Save conflict"); assert.equal(getLanguage(), "en");
  setLanguage("zh-CN", blocked); assert.equal(t("conflict"), "保存冲突");
});
