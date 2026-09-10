const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const themeBtn = $("#theme");
const savedTheme = localStorage.getItem("ocpp-theme");
if (savedTheme) document.body.dataset.theme = savedTheme;
themeBtn.addEventListener("click", () => {
  const next = document.body.dataset.theme === "dark" ? "light" : "dark";
  document.body.dataset.theme = next;
  localStorage.setItem("ocpp-theme", next);
});

$$("nav button").forEach((btn) => {
  btn.addEventListener("click", () => {
    $$("nav button").forEach((b) => b.classList.toggle("on", b === btn));
    $$(".view").forEach((v) => v.classList.toggle("on", v.id === btn.dataset.view));
  });
});

function nodeEl(id) {
  return document.querySelector(`[data-node="${id}"]`);
}

function center(id) {
  const el = typeof id === "string" ? nodeEl(id) : id;
  if (!el) return null;
  const r = el.getBoundingClientRect();
  const stage = $("#stage").getBoundingClientRect();
  return { x: r.left - stage.left + r.width / 2, y: r.top - stage.top + r.height / 2 };
}

function glow(id) {
  const el = nodeEl(id);
  if (!el) return;
  el.classList.add("glow");
  setTimeout(() => el.classList.remove("glow"), 900);
}

const SHIM_OF = { enphase: "enphase-shim", everhome: "everhome-shim", monta: "monta-shim" };
const BACKEND_OF = { "enphase-shim": "enphase", "everhome-shim": "everhome", "monta-shim": "monta" };
const HOP_MS = 1800;
const WIRES = [
  ["charger", "joulo"],
  ["joulo", "dummy"],
  ["joulo", "enphase-shim"],
  ["joulo", "everhome-shim"],
  ["joulo", "monta-shim"],
  ["enphase-shim", "enphase"],
  ["everhome-shim", "everhome"],
  ["monta-shim", "monta"],
  ["enphase-shim", "trash", "drop"],
  ["everhome-shim", "trash", "drop"],
  ["monta-shim", "trash", "drop"],
];

function hopsFor(event) {
  const src = event.src;
  const dst = event.fate === "drop" ? "trash" : event.dst;
  if (src === "charger") {
    if (dst === "dummy" || dst === "joulo") return ["charger", "joulo", dst === "joulo" ? "joulo" : "dummy"].filter((v, i, a) => a.indexOf(v) === i);
    if (SHIM_OF[dst]) return ["charger", "joulo", SHIM_OF[dst], dst];
    if (BACKEND_OF[dst]) return ["charger", "joulo", dst, BACKEND_OF[dst]];
    return ["charger", "joulo", dst];
  }
  if (event.fate === "drop") {
    if (SHIM_OF[src]) return [src, SHIM_OF[src], "trash"];
    if (BACKEND_OF[src]) return [src, "trash"];
    return [src, "joulo", "trash"];
  }
  if (src === "dummy") return ["dummy", "joulo", "charger"];
  if (SHIM_OF[src]) return [src, SHIM_OF[src], "joulo", "charger"];
  if (BACKEND_OF[src]) return [src, "joulo", "charger"];
  return [src, dst];
}

function drawWires() {
  const svg = $("#wires");
  if (!svg) return;
  const stage = $("#stage").getBoundingClientRect();
  svg.setAttribute("viewBox", `0 0 ${stage.width} ${stage.height}`);
  svg.replaceChildren();
  for (const [a, b, kind] of WIRES) {
    const from = center(a);
    const to = center(b);
    if (!from || !to) continue;
    const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
    const mx = (from.x + to.x) / 2;
    const my = (from.y + to.y) / 2;
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    path.setAttribute("d", `M ${from.x} ${from.y} Q ${mx - dy * 0.08} ${my + dx * 0.08} ${to.x} ${to.y}`);
    path.dataset.from = a;
    path.dataset.to = b;
    if (kind) path.classList.add(kind);
    svg.appendChild(path);
  }
}

function wirePath(fromId, toId) {
  return $(`#wires path[data-from="${fromId}"][data-to="${toId}"]`)
    || $(`#wires path[data-from="${toId}"][data-to="${fromId}"]`);
}

function animateHop(chip, fromId, toId) {
  const path = wirePath(fromId, toId);
  const from = center(fromId);
  const to = center(toId);
  if (!from || !to) return Promise.resolve();
  const reverse = path && path.dataset.from === toId;
  return new Promise((resolve) => {
    const t0 = performance.now();
    const len = path ? path.getTotalLength() : 0;
    function tick(now) {
      const t = Math.min(1, (now - t0) / HOP_MS);
      if (path && len) {
        const pt = path.getPointAtLength(reverse ? len * (1 - t) : len * t);
        chip.style.left = `${pt.x}px`;
        chip.style.top = `${pt.y}px`;
      } else {
        chip.style.left = `${from.x + (to.x - from.x) * t}px`;
        chip.style.top = `${from.y + (to.y - from.y) * t}px`;
      }
      if (t < 1) requestAnimationFrame(tick);
      else resolve();
    }
    requestAnimationFrame(tick);
  });
}

async function spawnChip(event) {
  const hops = hopsFor(event).filter((id) => nodeEl(id));
  if (hops.length < 2) return;
  glow(hops[0]);
  const start = center(hops[0]);
  const chip = document.createElement("div");
  chip.className = "chip" + (event.fate === "drop" ? " drop" : event.src === "charger" ? "" : " amber");
  chip.textContent = event.label || event.action;
  chip.style.left = `${start.x}px`;
  chip.style.top = `${start.y}px`;
  $("#stage").appendChild(chip);
  for (let i = 1; i < hops.length; i++) {
    if (hops[i] === "trash") $("#trash").classList.add("open");
    await animateHop(chip, hops[i - 1], hops[i]);
    glow(hops[i]);
  }
  if (event.fate === "drop") setTimeout(() => $("#trash").classList.remove("open"), 320);
  chip.remove();
}

function logEvent(event) {
  const li = document.createElement("li");
  if (event.fate === "drop") li.className = "drop";
  li.textContent = `${event.src} → ${event.fate === "drop" ? "trash" : event.dst}  ${event.label}`;
  const ol = $("#log");
  ol.prepend(li);
  while (ol.children.length > 40) ol.lastChild.remove();
}

function formatAmp(value) {
  if (value == null || Number.isNaN(Number(value))) return "–";
  const n = Number(value);
  if (Math.abs(n - Math.round(n)) < 0.05) return String(Math.round(n));
  return n.toFixed(1);
}

function applyCharger(state) {
  const site = $("#site");
  if (!site || !state) return;
  site.classList.toggle("plugged", !!state.plugged);
  site.classList.toggle("charging", !!state.charging);
  if (state.status) site.dataset.status = state.status;
  const stats = $("#ev-stats");
  if (!stats) return;
  if (!state.charging) return;
  const phases = Number(state.phases || 0);
  const amps = state.amps || [];
  stats.querySelector("[data-k=phases]").textContent = phases ? `${phases} Phasen` : "Phasen –";
  stats.querySelector("[data-k=amps]").textContent =
    `L1 ${formatAmp(amps[0])} · L2 ${formatAmp(amps[1])} · L3 ${formatAmp(amps[2])} A`;
  stats.querySelector("[data-k=kw]").textContent = `${Number(state.kw || 0).toFixed(1)} kW`;
}

function connectWs() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${proto}://${location.host}/ws/events`);
  ws.onmessage = (m) => {
    const event = JSON.parse(m.data);
    if (event.charger) applyCharger(event.charger);
    if (event.type === "snapshot") return;
    spawnChip(event);
    logEvent(event);
  };
  ws.onclose = () => setTimeout(connectWs, 1500);
}

const CATALOG = [
  { id: "enphase", label: "Enphase IQ Energy Router", profile: "answer", appendCpid: true },
  { id: "everhome", label: "EverHome", profile: "lax-ws", appendCpid: false },
  { id: "monta", label: "Monta", profile: "answer", appendCpid: false },
  { id: "joulo", label: "Joulo", profile: "mirror", appendCpid: true },
  { id: "homeassistant", label: "Home Assistant OCPP", profile: "answer", appendCpid: true },
  { id: "steve", label: "SteVe", profile: "answer", appendCpid: true },
  { id: "custom", label: "Custom CSMS", profile: "answer", appendCpid: true },
];

const ALLOW_COMMANDS = [
  { id: "TriggerMessage:StatusNotification", label: "StatusNotification", group: "trigger" },
  { id: "TriggerMessage:MeterValues", label: "MeterValues", group: "trigger" },
  { id: "TriggerMessage:Heartbeat", label: "Heartbeat", group: "trigger" },
  { id: "TriggerMessage:BootNotification", label: "BootNotification", group: "trigger" },
  { id: "GetConfiguration", label: "Get config", group: "read" },
  { id: "GetLocalListVersion", label: "RFID list version", group: "read" },
  { id: "ChangeConfiguration", label: "Change config", group: "control" },
  { id: "RemoteStartTransaction", label: "Start charging", group: "control" },
  { id: "RemoteStopTransaction", label: "Stop charging", group: "control" },
  { id: "SetChargingProfile", label: "Set profile", group: "control" },
  { id: "ClearChargingProfile", label: "Clear profile", group: "control" },
  { id: "ChangeAvailability", label: "Availability", group: "control" },
  { id: "Reset", label: "Reset", group: "control" },
  { id: "UnlockConnector", label: "Unlock", group: "control" },
];

function allowToggles(selected = []) {
  const set = new Set(selected);
  const groups = [
    { key: "trigger", title: "Trigger — forward to charger" },
    { key: "read", title: "Read" },
    { key: "control", title: "Control" },
  ];
  return groups.map((group) => {
    const cmds = ALLOW_COMMANDS.filter((c) => c.group === group.key);
    if (!cmds.length) return "";
    const chips = cmds.map((c) => `
      <label class="allow-toggle ${c.group}">
        <input type="checkbox" name="allow" value="${c.id}" ${set.has(c.id) ? "checked" : ""}>
        ${c.label}
      </label>`).join("");
    return `<span class="allow-group">${group.title}</span>${chips}`;
  }).join("");
}

function secondaryRow(sec = {}) {
  const wrap = document.createElement("div");
  wrap.className = "sec-row";
  wrap.innerHTML = `
    <label>Backend
      <select name="kind">
        ${CATALOG.map((c) => `<option value="${c.id}">${c.label}</option>`).join("")}
      </select>
    </label>
    <label>URL <input name="url" placeholder="ws://host:port"></label>
    <button type="button" class="rm">Remove</button>
    <div class="sec-allow">${allowToggles(sec.allowedCommands || [])}</div>
  `;
  wrap.querySelector("[name=kind]").value = sec.kind || sec.id || "custom";
  wrap.querySelector("[name=url]").value = sec.url || "";
  wrap.querySelector(".rm").onclick = () => wrap.remove();
  return wrap;
}

async function loadSetup() {
  const cfg = await (await fetch("/api/config")).json();
  const form = $("#setup-form");
  form.cpid.value = cfg.charger?.cpid || "";
  form.listenPort.value = cfg.charger?.listenPort || 9100;
  form.statusIntervalChargingSec.value = cfg.charger?.statusIntervalChargingSec ?? 60;
  form.statusIntervalIdleSec.value = cfg.charger?.statusIntervalIdleSec ?? 7;
  form.primaryKind.value = cfg.primary?.kind || "dummy";
  form.primaryUrl.value = cfg.primary?.url || "";
  const box = $("#seconds");
  box.innerHTML = "";
  (cfg.secondaries || []).forEach((s) => box.appendChild(secondaryRow(s)));
}

$("#add-secondary").addEventListener("click", () => {
  $("#seconds").appendChild(secondaryRow({ kind: "custom" }));
});

$("#setup-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  const secondaries = $$(".sec-row", form).map((row) => {
    const kind = row.querySelector("[name=kind]").value;
    const meta = CATALOG.find((c) => c.id === kind) || CATALOG.at(-1);
    return {
      id: kind,
      kind,
      label: meta.label,
      url: row.querySelector("[name=url]").value,
      appendCpid: meta.appendCpid,
      profile: meta.profile,
      enabled: true,
      allowedCommands: $$("[name=allow]:checked", row).map((el) => el.value),
    };
  });
  const body = {
    charger: {
      id: "gemini",
      label: "go-e Gemini",
      cpid: form.cpid.value,
      listenPort: Number(form.listenPort.value),
      statusIntervalChargingSec: Number(form.statusIntervalChargingSec.value),
      statusIntervalIdleSec: Number(form.statusIntervalIdleSec.value),
    },
    primary: { kind: form.primaryKind.value, label: form.primaryKind.value, url: form.primaryUrl.value },
    secondaries,
  };
  const res = await fetch("/api/config", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const msg = $("#save-msg");
  msg.hidden = false;
  msg.textContent = res.ok ? "Saved. Allowed commands and status intervals apply immediately. Restart the stack only after changing URLs." : "Save failed.";
});

connectWs();
loadSetup();
fetch("/api/charger").then((r) => r.json()).then(applyCharger).catch(() => {});
drawWires();
window.addEventListener("resize", drawWires);
if (window.ResizeObserver) new ResizeObserver(drawWires).observe($("#stage"));
