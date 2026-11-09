import { defaultMapping, encodeMapping, ProtocolError } from "./protocol.mjs?v=1.2";
import { connectBluetooth, getAuthorizedDevices, DiagnosticPoller } from "./manager.mjs?v=1.2";
import { t, messages, sources, targets, blockReasons, getLanguage, setLanguage, initialLanguage, localize } from "./i18n.mjs?v=1.2";

const $ = (id) => document.getElementById(id);
const node = (tag, className = "", text) => {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
};
let storage;
try { storage = window.localStorage; } catch { /* Storage is optional. */ }
setLanguage(initialLanguage(navigator.language, storage));
const secure = window.isSecureContext && ["https:", "http:"].includes(window.location.protocol);
const compatible = secure && Boolean(navigator.bluetooth?.requestDevice);
let session = null, busy = false, draftDirty = false, mappingState = "preview", mappingError = null;
let lastRead = null, diagnostic = null, notice = null;
let authorizedDevices = [];
const canListDevices = compatible && typeof navigator.bluetooth.getDevices === "function";
const activity = [];
const connected = () => Boolean(session && !session.channel.closed && session.device.gatt.connected);
const hex32 = (n) => `0x${n.toString(16).padStart(8, "0")}`;
const selects = Array.from({ length: 16 }, (_, i) => {
  const label = node("label"), name = node("span"), select = node("select");
  select.id = `mapping-${i}`;
  for (let value = 0; value < 18; value++) { const option = node("option"); option.value = value; select.append(option); }
  label.append(name, select);
  return { label, name, select };
});
for (const [key, indices] of [["groupFace", [0, 1, 2, 3]], ["groupShoulders", [4, 5, 14, 15]],
  ["groupSystem", [6, 7, 8]], ["groupExtra", [9, 10, 11, 12, 13]]]) {
  const group = node("section", "binding-group"), heading = node("h3"), grid = node("div", "mapping-grid");
  heading.dataset.i18n = key; heading.id = key;
  group.setAttribute("aria-labelledby", key);
  grid.append(...indices.map((i) => selects[i].label));
  group.append(heading, grid); $("mapping-buttons").append(group);
}
const faceLayouts = { xbox: [1, 2, 3, 4], swapped: [2, 1, 4, 3] };
const poller = new DiagnosticPoller(() => perform(readDiagnostics, { quiet: true }),
  () => connected() && !busy && $("poll").checked && !document.hidden);

function errorText(error) {
  let text = messages[error?.code] ? t(error.code) : error?.message || String(error);
  if (error?.cause?.message) text += ` (${error.cause.name}: ${error.cause.message})`;
  return error?.hostStatus !== undefined ? `${text} hostStatus=${hex32(error.hostStatus)}` : text;
}
function setNotice(key, params = {}, success = false) { notice = key ? { key, params, success } : null; renderNotice(); }
function renderNotice() {
  $("notice").hidden = !notice;
  $("notice").classList.toggle("success", Boolean(notice?.success));
  if (notice) $("notice").textContent = notice.error ? errorText(notice.error) + (notice.error.needsRead ? `\n${t("verifyAgain")}` : "") : t(notice.key, notice.params);
}
function log(key, params = {}, error = null) {
  activity.push({ key, params, error, at: new Date() });
  if (activity.length > 80) activity.shift();
  renderLog();
}
function renderLog() {
  $("log").replaceChildren(...activity.map((entry) => {
    const li = node("li", entry.error ? "error" : "");
    const time = node("time", "", entry.at.toLocaleTimeString(getLanguage(), { hour12: false }));
    time.dateTime = entry.at.toISOString();
    li.append(time, document.createTextNode(entry.error ? t("errorDetail", { detail: errorText(entry.error) }) : t(entry.key, entry.params)));
    return li;
  }));
  $("log").scrollTop = $("log").scrollHeight;
}
function resumePolling() {
  poller.stop();
  if (connected() && $("poll").checked && !busy && !document.hidden) poller.start();
}
async function perform(operation, { quiet = false } = {}) {
  if (busy) return;
  busy = true; poller.stop();
  if (!quiet) setNotice(null);
  render();
  try { await operation(); }
  catch (error) {
    if (error.code === "chooserEmpty") { setNotice("chooserEmpty"); log("chooserEmpty"); }
    else {
      notice = { error }; renderNotice(); log("error", {}, error);
      if (!quiet && !session?.client.snapshot && !error.saveState) mappingError = error;
    }
  } finally { busy = false; render(); resumePolling(); }
}
function onSaveState(detail) {
  mappingState = detail.state; mappingError = detail.error ?? null;
  if (["staging", "saving", "persisted", "verified", "conflict", "failed", "unknown", "persistedUnverified"].includes(detail.state)) log(detail.state);
  renderMappingState();
}
function onDisconnected() {
  poller.stop();
  session?.close(); session = null;
  log("disconnected"); render();
}
function fillMapping(m) {
  selects.forEach(({ select }, i) => { select.value = m.bindings[i]; });
  $("stick-deadzone").value = m.stickDeadzone; $("pad-deadzone").value = m.padDeadzone;
  $("dpad-threshold").value = m.dpadThreshold; $("dpad-angle").value = m.diagonalAngle;
  $("invert-x").checked = m.invertRightX; $("invert-y").checked = m.invertRightY; $("require-click").checked = m.requireLeftPadClick;
  renderAngle(); renderLayout();
}
function readForm() {
  const value = { bindings: selects.map(({ select }) => Number(select.value)),
    stickDeadzone: $("stick-deadzone").valueAsNumber, padDeadzone: $("pad-deadzone").valueAsNumber,
    dpadThreshold: $("dpad-threshold").valueAsNumber, diagonalAngle: $("dpad-angle").valueAsNumber,
    invertRightX: $("invert-x").checked, invertRightY: $("invert-y").checked, requireLeftPadClick: $("require-click").checked };
  encodeMapping(value);
  return value;
}
async function readMapping({ keepDraft = false } = {}) {
  const active = session;
  if (!active) throw new ProtocolError("disconnected");
  mappingState = "idle"; mappingError = null; renderMappingState();
  const snapshot = await active.client.readMapping();
  lastRead = snapshot;
  if (!keepDraft) { fillMapping(snapshot.mapping); draftDirty = false; }
  mappingState = keepDraft ? "dirty" : "loaded"; mappingError = null; log("loaded");
  if (keepDraft) log("draftKept");
}
async function readDiagnostics() {
  const active = session;
  if (!active) throw new ProtocolError("disconnected");
  diagnostic = await active.client.readDiagnostics();
  renderDiagnostics();
}
function renderAngle() {
  const angle = $("dpad-angle").valueAsNumber;
  $("angle-value").textContent = t(angle ? "angleValue" : "angleOff", { angle, cardinal: 90 - angle });
  $("dpad-angle").setAttribute("aria-valuetext", $("angle-value").textContent);
}
function renderLayout() {
  $("face-layout").value = Object.keys(faceLayouts).find((key) =>
    faceLayouts[key].every((value, i) => Number(selects[i].select.value) === value)) ?? "custom";
  for (let i = 0; i < 4; i++) $("face-target-" + i).textContent = targets[getLanguage()][Number(selects[i].select.value)];
}
function renderMappingState() {
  $("save-state").textContent = t(mappingState);
  $("save-state").className = `pill ${["verified", "loaded"].includes(mappingState) ? "good" :
    ["conflict", "failed", "unknown", "persistedUnverified"].includes(mappingState) ? "error" : ["idle", "preview"].includes(mappingState) ? "" : "pending"}`;
  const key = ["staging", "saving", "persisted"].includes(mappingState) ? "savingDetail" :
    mappingState === "persistedUnverified" ? "persistedDetail" :
      ["conflict", "failed", "unknown"].includes(mappingState) ? "verifyAgain" : `${mappingState}Detail`;
  $("mapping-status").textContent = (mappingError ? `${errorText(mappingError)}\n` : "") + t(key) +
    (!connected() ? `\n${t("offlineSave")}` : "");
  $("mapping-token").textContent = lastRead ? t("token", { token: hex32(lastRead.token), hostStatus: hex32(lastRead.hostStatus) }) : "";
}
function render() {
  const live = connected(), ready = live && Boolean(session.client.snapshot);
  $("connection-status").textContent = `${t(live ? "connected" : "disconnected")}${busy ? ` · ${t("working")}` : ""}`;
  $("connect").disabled = !compatible || busy || live;
  $("disconnect").disabled = !live;
  renderDevices();
  $("mapping-offline").hidden = live;
  $("mapping-controls").disabled = $("defaults").disabled = busy;
  $("save").disabled = !ready || busy;
  $("read-mapping").disabled = $("refresh-diag").disabled = !live || busy;
  $("export-diag").disabled = !diagnostic;
  $("environment").textContent = t(compatible ? "environment" : secure ? "unsupported" : "insecure");
  renderMappingState(); renderNotice(); renderUpdated();
}
function renderDevices() {
  const select = $("authorized-devices"), previous = select.value;
  select.replaceChildren(...authorizedDevices.map((device, index) => {
    const option = node("option", "", `${device.name || t("unnamedDevice")} · ${index + 1}`);
    option.value = device.id;
    return option;
  }));
  if (!authorizedDevices.length) { const option = node("option", "", t("noAuthorizedDevices")); option.value = ""; select.append(option); }
  if (authorizedDevices.some((device) => device.id === previous)) select.value = previous;
  select.disabled = busy || connected() || !authorizedDevices.length;
  $("reconnect").disabled = !compatible || select.disabled;
  $("refresh-devices").disabled = !canListDevices || busy || connected();
  $("device-list-help").textContent = t(canListDevices ? "authorizedHelp" : "pageDevicesOnly");
}
async function refreshDevices() {
  const devices = await getAuthorizedDevices(navigator.bluetooth);
  if (devices !== null) authorizedDevices = devices;
  renderDevices();
}
function rememberDevice(device) {
  authorizedDevices = [...authorizedDevices.filter((known) => known.id !== device.id), device];
  renderDevices(); $("authorized-devices").value = device.id;
}
function renderUpdated() {
  $("updated").textContent = diagnostic ? t("updated", { time: new Date(diagnostic.capturedAt).toLocaleTimeString(getLanguage(), { hour12: false }), stale: connected() ? "" : t("stale") }) : t("noSample");
}
function renderDiagnostics() {
  $("metrics").replaceChildren();
  $("report-empty").hidden = Boolean(diagnostic); $("report-display").hidden = !diagnostic;
  $("diagnostic-table").replaceChildren();
  if (!diagnostic) return;
  const d = diagnostic;
  const cards = [
    ["metricRX", `${d.input.rx} / ${d.input.parseOK}`, t("metricLast", { length: d.input.lastLength, received: hex32(d.input.received) })],
    ["metricBad", d.input.parseBad, `ticks ${d.identity.ticks}`],
    ["metricHVX", `${d.hvx.success} / ${d.hvx.attempts}`, t("metricResult", { result: hex32(d.hvx.lastResult) })],
    ["metricBlock", blockReasons[getLanguage()][d.link.blockReason] ?? String(d.link.blockReason), `CCCD ${hex32(d.link.cccd)} · security ${d.hvx.securityModeLevel}`],
  ];
  for (const [label, value, note] of cards) {
    const card = node("div", "metric");
    card.append(node("div", "metric-label", t(label)), node("div", "metric-value", value), node("div", "metric-note", note));
    $("metrics").append(card);
  }
  $("axes").replaceChildren(...["lx", "ly", "rx", "ry", "lt", "rt"].map((key) => {
    const axis = node("div", "axis"), meter = node("meter");
    meter.min = 0; meter.max = key.endsWith("t") ? 1023 : 65535; meter.value = d.report[key];
    meter.setAttribute("aria-label", key.toUpperCase());
    axis.append(node("span", "", key.toUpperCase()), node("span", "mono", d.report[key]), meter);
    return axis;
  }));
  const hats = [t("neutral"), "↑ N", "↗ NE", "→ E", "↘ SE", "↓ S", "↙ SW", "← W", "↖ NW"];
  $("hat").textContent = `${d.report.hat} · ${hats[d.report.hat] ?? "?"}`;
  const buttons = [["A", 1], ["B", 2], ["X", 8], ["Y", 16], ["LB", 0x40], ["RB", 0x80], ["View", 0x400], ["Menu", 0x800], ["Guide", 0x1000], ["L3", 0x2000], ["R3", 0x4000]];
  $("xbox-buttons").replaceChildren(...buttons.map(([label, mask]) => node("span", `chip${d.report.buttons & mask ? " active" : ""}`, `${d.report.buttons & mask ? "● " : "○ "}${label}`)),
    node("span", `chip${d.report.share ? " active" : ""}`, `${d.report.share ? "● " : "○ "}Share`));
  $("report-hex").textContent = d.report.hex;
  $("raw-buttons").textContent = t("rawButtons", { buttons: hex32(d.raw.buttons), suppress: hex32(d.raw.suppress), header: hex32(d.raw.lastHeader), xbox: hex32(d.report.buttons) });
  // Stable protocol field names keep exported JSON and the table easy to compare.
  for (const group of ["identity", "input", "hvx", "link", "raw", "report"]) {
    for (const [key, value] of Object.entries(d[group])) {
      const row = node("tr"); row.append(node("td", "", `${group}.${key}`), node("td", "", String(value))); $("diagnostic-table").append(row);
    }
  }
  renderUpdated();
}
function translate() {
  document.documentElement.lang = getLanguage(); document.title = t("title");
  $("language").value = getLanguage(); localize(document);
  selects.forEach(({ name, select }, i) => {
    name.textContent = sources[getLanguage()][i];
    Array.from(select.options).forEach((option, j) => { option.textContent = targets[getLanguage()][j]; });
  });
  renderAngle(); renderLayout(); render(); renderDiagnostics(); renderLog();
}

async function openController(device) {
  session?.close(); session = null;
  session = await connectBluetooth(navigator.bluetooth, onSaveState, onDisconnected, { device, onSelected: rememberDevice });
  lastRead = null; diagnostic = null; mappingError = null;
  renderDiagnostics(); log("connected");
  await readMapping({ keepDraft: draftDirty });
  await readDiagnostics();
}
$("connect").addEventListener("click", () => perform(() => openController()));
$("reconnect").addEventListener("click", () => {
  const device = authorizedDevices.find((known) => known.id === $("authorized-devices").value);
  if (device) perform(() => openController(device));
});
$("refresh-devices").addEventListener("click", () => perform(refreshDevices));
$("disconnect").addEventListener("click", () => { session?.close(); onDisconnected(); });
$("read-mapping").addEventListener("click", () => perform(readMapping));
$("defaults").addEventListener("click", () => {
  setNotice(null);
  fillMapping(defaultMapping()); draftDirty = true; mappingState = "defaultsFilled"; mappingError = null; renderMappingState();
});
$("face-layout").addEventListener("change", () => {
  const layout = faceLayouts[$("face-layout").value];
  if (!layout) return;
  layout.forEach((target, i) => { selects[i].select.value = target; });
  setNotice(null); draftDirty = true; mappingState = "dirty"; mappingError = null;
  renderLayout(); renderMappingState();
});
$("mapping-controls").addEventListener("input", (event) => {
  setNotice(null);
  draftDirty = true; mappingState = "dirty"; mappingError = null; renderAngle();
  // The preset's change handler updates the four bindings before reflecting it.
  if (event.target !== $("face-layout")) renderLayout();
  renderMappingState();
});
$("save").addEventListener("click", () => perform(async () => {
  const mapping = readForm();
  if (!session) throw new ProtocolError("disconnected");
  const result = await session.client.saveMapping(mapping);
  lastRead = result; fillMapping(result.mapping); draftDirty = false; setNotice("verifiedDetail", {}, true);
}));
$("refresh-diag").addEventListener("click", () => perform(readDiagnostics));
$("poll").addEventListener("change", resumePolling);
document.addEventListener("visibilitychange", resumePolling);
$("export-diag").addEventListener("click", () => {
  if (!diagnostic) return;
  const data = { format: "sc2015-xbox-ble-diagnostics", version: 1, exportedAt: new Date().toISOString(),
    connected: connected(), sampling: "sequential-pages-not-atomic", diagnostics: diagnostic };
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2) + "\n"], { type: "application/json" }));
  const a = node("a"); a.href = url; a.download = "sc2015-xbox-ble-diagnostics.json";
  document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
  log("exported");
});
$("language").addEventListener("change", () => { setLanguage($("language").value, storage); translate(); });
$("clear-log").addEventListener("click", () => { activity.length = 0; renderLog(); });
window.addEventListener("pagehide", () => { poller.stop(); session?.close(); session = null; });
if (!compatible) setNotice(secure ? "unsupported" : "insecure");
fillMapping(defaultMapping());
translate();
if (canListDevices) perform(refreshDevices, { quiet: true });
