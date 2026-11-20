const bridge = window.AstrBotPluginPage;
const $ = (id) => document.getElementById(id);
const state = { users: [], selected: null, overview: null, config: {} };

function toast(msg, isErr = false) {
  const el = $("toast");
  el.textContent = msg;
  el.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (el.className = "toast"), 3200);
}

function unwrap(res) {
  if (res && typeof res === "object" && "status" in res && "data" in res) return res.data;
  return res;
}

async function apiGet(endpoint, params = {}) {
  if (!bridge) throw new Error("AstrBot 插件页面桥未注入");
  const res = unwrap(await bridge.apiGet(endpoint, params));
  if (res && res.ok === false) throw new Error(res.error || "请求失败");
  return res || {};
}

async function apiPost(endpoint, body = {}) {
  if (!bridge) throw new Error("AstrBot 插件页面桥未注入");
  const res = unwrap(await bridge.apiPost(endpoint, body));
  if (res && res.ok === false) throw new Error(res.error || "请求失败");
  return res || {};
}

const esc = (s) => String(s == null ? "" : s)
  .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
  .replace(/"/g, "&quot;").replace(/'/g, "&#39;");

function fmtTime(ts) {
  if (!ts) return "–";
  const d = new Date(Number(ts) * (Number(ts) < 1e12 ? 1000 : 1));
  if (isNaN(d.getTime())) return String(ts).slice(0, 19);
  const now = Date.now();
  const diff = now - d.getTime();
  if (diff >= 0 && diff < 60000) return "刚刚";
  if (diff >= 0 && diff < 3600000) return `${Math.floor(diff / 60000)} 分钟前`;
  if (diff >= 0 && diff < 86400000) return `${Math.floor(diff / 3600000)} 小时前`;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getMonth() + 1}/${d.getDate()} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function fmtBytes(n) {
  n = Number(n || 0);
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / 1048576).toFixed(2)} MB`;
}

function badge(stage) {
  const labels = { normal: "初识", warming: "渐熟", attached: "亲近", hurt: "受损", refusing: "疏离" };
  return `<span class="badge ${esc(stage)}">${esc(labels[stage] || stage || "初识")}</span>`;
}

function switchTab(name) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.tab === name));
  document.querySelectorAll(".tab-content").forEach((c) => c.classList.toggle("active", c.id === `tab-${name}`));
  if (name === "users" && !state.users.length) loadUsers();
  if (name === "config" && !Object.keys(state.config).length) loadConfig();
}

async function loadOverview() {
  const data = await apiGet("overview");
  state.overview = data;
  $("version").textContent = data.version || "–";
  const enabled = !!data.enabled;
  const pill = $("healthPill");
  pill.textContent = enabled ? "运行中" : "已停用";
  pill.className = "pill " + (enabled ? "on" : "off");

  const cfg = data.config || {};
  $("statUsers").textContent = data.users ?? 0;
  $("statFiles").textContent = data.timeline?.files ?? 0;
  $("statBytes").textContent = fmtBytes(data.timeline?.bytes || 0);
  $("statProactive").textContent = cfg.proactive_enabled ? "开" : "关";
  $("statSemantic").textContent = cfg.semantic_router_enabled ? "开" : "关";
  $("statConfidence").textContent = `阈值 ${cfg.semantic_router_confidence ?? "–"}`;

  $("statusList").innerHTML = [
    ["插件版本", data.version || "–"],
    ["总开关", enabled ? "启用" : "停用"],
    ["状态层", cfg.state_layer_enabled ? "启用" : "停用"],
    ["语义兜底", cfg.semantic_router_enabled ? "启用" : "停用"],
    ["主动消息", cfg.proactive_enabled ? "启用" : "停用"],
    ["已配置键", `${Object.keys(cfg).length} 项`],
  ].map(([k, v]) => `<div class="kv-row"><span>${esc(k)}</span><b>${esc(v)}</b></div>`).join("");

  $("cronBody").innerHTML = (data.cron || []).map((j) => `
    <tr><td>${esc(j.name)}</td><td>${esc(j.cron)}</td><td>${esc(j.timezone || "–")}</td>
    <td>${j.enabled ? '<span class="badge attached">启用</span>' : '<span class="badge hurt">停用</span>'}</td></tr>
  `).join("") || '<tr><td colspan="4" class="empty">暂无任务</td></tr>';

  const chips = [
    ["状态层预算", cfg.state_layer_max_tokens],
    ["记忆锚点", cfg.state_layer_max_anchors],
    ["每日主动", cfg.proactive_max_per_day],
    ["免打扰", `${cfg.proactive_quiet_start_hour ?? "–"}–${cfg.proactive_quiet_end_hour ?? "–"} 点`],
    ["记忆上限", cfg.relation_memory_max_items],
    ["命理服务", cfg.mingbu_api_url || "未配置"],
  ];
  $("configChips").innerHTML = chips.map(([k, v]) => `<span class="chip">${esc(k)}<b>${esc(v)}</b></span>`).join("");
}

async function loadUsers() {
  const data = await apiGet("users");
  state.users = data.users || [];
  renderUsers();
}

function renderUsers() {
  const q = ($("userSearch").value || "").trim().toLowerCase();
  const list = state.users.filter((u) => !q || u.umo.toLowerCase().includes(q) || String(u.topic || "").toLowerCase().includes(q));
  $("userList").innerHTML = list.map((u) => `
    <div class="user-item ${state.selected === u.umo ? "active" : ""}" data-umo="${esc(u.umo)}">
      <div class="user-top"><span class="user-umo">${esc(u.umo)}</span>${badge(u.stage)}</div>
      <div class="user-meta">${esc(u.topic || "无话题")} · 未闭环 ${u.open_loops || 0} · ${fmtTime(u.last_message)}</div>
    </div>
  `).join("") || '<div class="empty">暂无用户数据</div>';
  document.querySelectorAll(".user-item").forEach((el) => {
    el.addEventListener("click", () => selectUser(el.dataset.umo));
  });
}

async function selectUser(umo) {
  state.selected = umo;
  renderUsers();
  $("timelineTitle").textContent = `时间线 · ${umo}`;
  await loadTimeline();
}

function brief(payload) {
  if (!payload || typeof payload !== "object") return String(payload || "");
  if (payload.body) return String(payload.body).slice(0, 90);
  if (payload.topic) return `话题：${payload.topic}`;
  if (payload.text) return String(payload.text).slice(0, 90);
  if (payload.content) return String(payload.content).slice(0, 90);
  if (payload.from || payload.to) return `${payload.from || "?"} → ${payload.to || "?"}`;
  try { return JSON.stringify(payload).slice(0, 90); } catch { return ""; }
}

async function loadTimeline() {
  if (!state.selected) return;
  const data = await apiGet("timeline", {
    umo: state.selected,
    limit: $("limitSelect").value,
    kind: $("kindFilter").value,
  });
  const rows = data.records || [];
  $("timelineBox").innerHTML = rows.slice().reverse().map((r) => `
    <details class="tl-row">
      <summary>
        <span class="tl-time">${esc(fmtTime(r.ts))}</span>
        <span class="tl-kind">${esc(r.kind || "event")}</span>
        <span class="tl-brief">${esc(brief(r.payload))}</span>
      </summary>
      <pre>${esc(JSON.stringify(r.payload || {}, null, 2).slice(0, 6000))}</pre>
    </details>
  `).join("") || '<div class="empty">该用户还没有时间线记录</div>';
}

async function loadConfig() {
  const data = await apiGet("config");
  state.config = data.config || {};
  fillConfig(state.config);
}

function fillConfig(cfg) {
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    if (!(key in cfg)) return;
    const v = cfg[key];
    if (el.type === "checkbox") el.checked = !!v;
    else el.value = v == null ? "" : v;
  });
}

function collectConfig() {
  const out = {};
  document.querySelectorAll("[data-key]").forEach((el) => {
    const key = el.dataset.key;
    const type = el.dataset.type || "string";
    if (type === "bool") out[key] = !!el.checked;
    else if (type === "int") out[key] = parseInt(el.value, 10) || 0;
    else if (type === "float") out[key] = parseFloat(el.value) || 0;
    else out[key] = el.value;
  });
  return out;
}

async function saveConfig(ev) {
  ev.preventDefault();
  const btn = $("saveBtn");
  btn.disabled = true;
  $("saveHint").textContent = "保存中…";
  try {
    const data = await apiPost("config", collectConfig());
    state.config = data.config || state.config;
    fillConfig(state.config);
    $("saveHint").textContent = data.message || "已保存";
    toast("配置已保存");
    await loadOverview();
  } catch (e) {
    $("saveHint").textContent = "";
    toast(e.message || "保存失败", true);
  } finally {
    btn.disabled = false;
  }
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => switchTab(t.dataset.tab)));
  $("refreshBtn").addEventListener("click", () => refreshAll());
  $("reloadBtn").addEventListener("click", () => loadConfig().then(() => toast("已重新读取")));
  $("configForm").addEventListener("submit", saveConfig);
  $("userSearch").addEventListener("input", renderUsers);
  $("kindFilter").addEventListener("change", loadTimeline);
  $("limitSelect").addEventListener("change", loadTimeline);

  if (!bridge) {
    $("healthPill").textContent = "桥未就绪";
    $("healthPill").className = "pill off";
    toast("请通过 AstrBot 插件页面打开本控制台", true);
    return;
  }
  refreshAll();
});

async function refreshAll() {
  try {
    await loadOverview();
    if (state.users.length || document.querySelector('#tab-users.active')) await loadUsers();
  } catch (e) {
    toast(e.message || "加载失败", true);
  }
}