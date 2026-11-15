const bridge = window.AstrBotPluginPage;
if (!bridge) { console.error("[mingbu] bridge NOT available!"); }

// 脱离 AstrBot 管理面板时，bridge 未注入，所有 API 调用将失败
if (!bridge) {
  console.warn("[mingbu] bridge NOT available! 当前页面需通过 AstrBot 管理面板（/admin/mingbu）访问。");
  // 注入一个占位 toast，让用户知道问题所在
  const warning = document.createElement("div");
  warning.className = "toast show err";
  warning.style.cssText = "position:fixed;top:20px;left:50%;transform:translateX(-50%);z-index:9999;max-width:90%;padding:12px 20px;border-radius:8px;background:#fee2e2;color:#991b1b;border:1px solid #fca5a5;font-size:14px;";
  warning.innerHTML = "⚠️ <b>管理面板未就绪</b>：当前页面需通过 <code>/admin/mingbu</code> 访问，<br>直接打开 HTML 文件无法调用后端 API。";
  document.body.prepend(warning);
  setTimeout(() => warning.remove(), 8000);
}

let CURRENT_UID = null;
let CONFIG_DATA = {};
let CONV_OFFSET = 0;
let CONV_LIMIT = 20;
let CONV_TOTAL = 0;
let ALL_USERS = [];

const $ = (id) => document.getElementById(id);

function toast(msg, isErr = false) {
  const t = $("toast");
  t.textContent = msg;
  t.className = "toast show" + (isErr ? " err" : "");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => (t.className = "toast"), 2600);
}

async function apiGet(endpoint, params) {
  const res = await bridge.apiGet(endpoint, params || {});
  if (res && res.status === "error") throw new Error(res.message || "请求失败");
  return res;
}
async function apiPost(endpoint, body) {
  if (!bridge) {
    throw new Error("bridge 对象未定义 (window.AstrBotPluginPage 不可用)");
  }
  const res = await bridge.apiPost(endpoint, body || {});
  if (res && res.status === "error") throw new Error(res.message || "请求失败");
  return res;
}

const RELATION_LABEL = {
  normal: "刚认识", warming: "渐熟", attached: "铁哥们",
  refusing: "想静静", hurt: "冷淡期",
};
function relationTag(mode) {
  const label = RELATION_LABEL[mode] || mode;
  const cls = mode === "attached" ? "attached" : mode === "warming" ? "warming" : mode === "hurt" ? "hurt" : "";
  return `<span class="relation-tag ${cls}">${label}</span>`;
}
function avatarText(name, uid) {
  if (name) return name.slice(0, 1);
  return (uid || "?").slice(0, 2);
}

function formatTime(ts) {
  if (!ts) return "–";
  const d = new Date(ts);
  if (isNaN(d.getTime())) return ts.slice(0, 10);
  const now = new Date();
  const diff = now - d;
  if (diff < 60000) return "刚刚";
  if (diff < 3600000) return Math.floor(diff / 60000) + " 分钟前";
  if (diff < 86400000) return Math.floor(diff / 3600000) + " 小时前";
  return `${d.getMonth() + 1}/${d.getDate()} ${d.getHours()}:${String(d.getMinutes()).padStart(2, "0")}`;
}

function escapeHtml(s) {
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function switchTab(tabName) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
  $(`tab-${tabName}`).classList.add("active");
  document.querySelector(`.tab[data-tab="${tabName}"]`).classList.add("active");

  if (tabName === "config") { loadConfig(); loadModelsTab(); }
  if (tabName === "characters") loadCharacters();
  if (tabName === "tokens") loadTokenStats();
  if (tabName === "conversation") loadConversationData();
  if (tabName === "diagnostics") loadDiagnostics();
}

async function loadDiagnostics() {
  const statusEl = $("capabilityStatus");
  if (!statusEl) return;
  try {
    const data = await apiGet("diagnostics");
    const stateLabel = { initialized: "已初始化", disabled: "已禁用", failed: "初始化失败", initializing: "初始化中" };
    statusEl.innerHTML = (data.capabilities || []).map(item => `
      <div class="diagnostic-row">
        <strong>${escapeHtml(item.name)}</strong>
        <span class="diagnostic-state ${escapeHtml(item.state)}">${stateLabel[item.state] || escapeHtml(item.state)}</span>
        <small>${escapeHtml(item.error || (item.config_key ? `配置：${item.config_key}` : "无额外说明"))}</small>
      </div>`).join("") || '<div class="empty-text">暂无能力状态</div>';
  } catch (e) {
    statusEl.innerHTML = `<div class="empty-text">诊断失败：${escapeHtml(e.message)}</div>`;
  }
}

async function runConnectivity(service) {
  const results = $("connectivityResults");
  if (!results) return;
  results.innerHTML = '<div class="loading-spinner"></div>';
  try {
    const data = await apiPost("connectivity", { service });
    const label = data.service || service;
    results.innerHTML = `<div class="diagnostic-row">
      <strong>${escapeHtml(label)}</strong>
      <span class="diagnostic-state ${data.ok ? "initialized" : "failed"}">${data.ok ? "检查通过" : "检查失败"}</span>
      <small>${escapeHtml(data.error || `配置完整，耗时 ${data.latency_ms ?? "–"} ms`)}${data.api_key ? ` · Key：${escapeHtml(data.api_key)}` : ""}</small>
    </div>`;
  } catch (e) {
    results.innerHTML = `<div class="empty-text">${escapeHtml(e.message)}</div>`;
  }
}

async function testMingbuBackend() {
  const statusEl = $("mingbuTestStatus");
  const btn = $("mingbuTestBtn");
  if (!statusEl) return;
  statusEl.className = "svc-test-status";
  statusEl.textContent = "测试中…";
  if (btn) btn.disabled = true;
  try {
    const r = await apiPost("mingbu_test", {});
    if (r && r.ok) {
      const provider = r.providerConfigured ? " · AI Provider 已就绪" : " · AI Provider 未配置（纯排盘仍可用）";
      statusEl.className = "svc-test-status ok";
      statusEl.textContent = `连通 · ${r.latency_ms ?? "–"}ms · ${r.url || ""}${provider}`;
    } else {
      statusEl.className = "svc-test-status err";
      const url = r && r.url ? ` · 地址 ${r.url}` : "";
      statusEl.textContent = `失败：${r && r.error ? r.error : "未知错误"}${url}`;
    }
  } catch (e) {
    statusEl.className = "svc-test-status err";
    statusEl.textContent = `请求异常：${e.message}`;
  } finally {
    if (btn) btn.disabled = false;
  }
}

async function loadStats() {
  try {
    const s = await apiGet("stats");
    // 版本号以后端 _version.py 为唯一数据源。HTML 里的值仅作离线降级占位，
    // 若此处不回填，面板页脚会长期停在打包时的旧版本（历史上曾滞后三个版本）。
    if (s.version) $("version").textContent = s.version;
    $("statUsers").textContent = s.active_users ?? "–";
    $("statGroups").textContent = s.groups ?? "–";
    $("statInteractions").textContent = s.total_interactions ?? "–";
    $("statMemories").textContent = s.total_memories ?? "–";

    if (s.bot_status) {
      const bs = s.bot_status;
      $("botMood").textContent = bs.mood ?? 62;
      $("botEnergy").textContent = bs.energy ?? 75;
      $("botSocial").textContent = bs.social ?? 50;
      $("botIntimacy").textContent = bs.intimacy ?? 0;
      $("personaName").textContent = bs.name ?? "灵犀";
      $("personaTraits").textContent = bs.traits ?? "神秘、敏锐、体贴";

      updateRing("botMood", bs.mood ?? 62);
      updateRing("botEnergy", bs.energy ?? 75);
      updateRing("botSocial", bs.social ?? 50);
      updateRing("botIntimacy", bs.intimacy ?? 0);
    }
  } catch (e) {
    console.error("加载统计失败:", e);
  }
}

function updateRing(elementId, value) {
  // 以 ring-value 的 id 定位环形容器，而不是按 data-value 取值——
  // 旧实现按 data-value==value 查找，非默认值（如 70）永远找不到对应环。
  const valueEl = document.getElementById(elementId);
  if (!valueEl) return;
  const ring = valueEl.closest(".status-ring");
  if (!ring) return;
  const circle = ring.querySelector("circle:last-child");
  if (!circle) return;
  const percent = Math.min(100, Math.max(0, value));
  const circumference = 2 * Math.PI * 32;
  const offset = circumference - (percent / 100) * circumference;
  circle.style.strokeDashoffset = offset;
  valueEl.textContent = Math.round(percent);
}

async function loadActivityFeed() {
  try {
    const data = await apiGet("recent_activity");
    const feed = $("activityFeed");
    if (!data.items || !data.items.length) {
      feed.innerHTML = '<div class="empty-text">暂无近期互动</div>';
      return;
    }
    feed.innerHTML = data.items.slice(0, 10).map(item => `
      <div class="activity-item">
        <div class="activity-avatar">${avatarText(item.user_name, item.user_uid)}</div>
        <div class="activity-content">
          <div class="activity-name">${escapeHtml(item.user_name || "未知")}</div>
          <div class="activity-text">${escapeHtml(item.type === "chat" ? "发送了消息" :
            item.type === "image" ? "发送了图片" :
            item.type === "dream" ? "生成了梦境" :
            item.type === "growth" ? "触发了成长反思" : item.type)}</div>
          <div class="activity-time">${formatTime(item.timestamp)}</div>
        </div>
      </div>
    `).join("");
  } catch (e) {
    $("activityFeed").innerHTML = '<div class="empty-text">加载失败</div>';
  }
}

async function loadGraph() {
  try {
    const g = await apiGet("graph");
    const groups = g.groups || [];
    const view = $("graphView");
    if (!groups.length) {
      view.innerHTML = '<div class="empty-text">暂无群组节点</div>';
      return;
    }
    view.innerHTML = groups.map(grp => `
      <div class="graph-group">
        <div class="graph-group-name">
          <span>${escapeHtml(grp.name)}</span>
          <span class="graph-group-meta">${grp.member_count} 人</span>
        </div>
        <div class="graph-members">
          ${(grp.members || []).slice(0, 10).map(m => `
            <span class="graph-member">${escapeHtml(m.slice(0, 10))}</span>
          `).join("")}
          ${grp.members && grp.members.length > 10 ? `
            <span class="graph-member">+${grp.members.length - 10}</span>
          ` : ""}
        </div>
      </div>
    `).join("");
  } catch (e) {
    $("graphView").innerHTML = '<div class="empty-text">加载失败</div>';
  }
}

async function loadUsers() {
  try {
    const data = await apiGet("users");
    ALL_USERS = data.users || [];
    const list = $("userList");
    if (!ALL_USERS.length) {
      list.innerHTML = '<div class="empty-state"><div class="empty-icon">💝</div><p>还没有用户羁绊</p></div>';
      return;
    }
    list.innerHTML = ALL_USERS.map(u => `
      <div class="user-item" data-uid="${escapeHtml(u.uid)}">
        <div class="user-avatar">${avatarText(u.name, u.uid)}</div>
        <div class="user-info">
          <div class="user-name">${escapeHtml(u.name || "(未命名)")}</div>
          <div class="user-meta">${relationTag(u.mode)} · UID: ${u.uid}</div>
        </div>
        <div class="user-score">${u.score?.toFixed(0) || 0}</div>
      </div>
    `).join("");
    list.querySelectorAll(".user-item").forEach(el =>
      el.addEventListener("click", () => selectUser(el.dataset.uid))
    );
    // 同步更新对话检视的用户选择器
    updateConvUserSelect();
  } catch (e) {
    $("userList").innerHTML = '<div class="empty-text">加载失败</div>';
  }
}

function updateConvUserSelect() {
  const sel = $("convUserSelect");
  const current = sel.value;
  sel.innerHTML = '<option value="">所有用户...</option>' +
    ALL_USERS.map(u => `<option value="${escapeHtml(u.uid)}">${escapeHtml(u.name || u.uid)}</option>`).join("");
  sel.value = current;
}

async function selectUser(uid) {
  CURRENT_UID = uid;
  document.querySelectorAll(".user-item").forEach(el =>
    el.classList.toggle("active", el.dataset.uid === uid)
  );
  await loadUserDetail(uid);
}

async function loadUserDetail(uid) {
  try {
    const d = await apiGet("user", { uid });
    const rel = d.relationship || {};
    const mem = d.recent_memories || [];
    const dreams = d.recent_dreams || [];

    $("userDetailEmpty").style.display = "none";
    $("userDetail").style.display = "block";

    $("detailAvatar").textContent = avatarText(d.profile?.name, uid);
    $("detailName").textContent = d.profile?.name || "未知用户";
    $("relationTag").textContent = RELATION_LABEL[rel.mode] || "刚认识";
    $("relationTag").className = `relation-tag ${rel.mode === "attached" ? "attached" : rel.mode === "warming" ? "warming" : rel.mode === "hurt" ? "hurt" : ""}`;
    $("detailUid").textContent = uid;

    const affinityScore = rel.score || 0;
    $("affinityScore").textContent = affinityScore.toFixed(0);
    $("affinityFill").style.width = `${Math.min(100, Math.max(0, affinityScore))}%`;

    $("interactionCount").textContent = rel.total_interactions || 0;
    $("intimacyMoments").textContent = rel.intimacy_moments || 0;
    $("lastInteract").textContent = formatTime(rel.last_interaction);

    const memHtml = mem.length ? mem.slice(0, 5).map(m => `
      <div class="memory-item">
        <div class="memory-time">${formatTime(m.timestamp)} · ${m.type}</div>
        <div class="memory-content">${escapeHtml(m.content.slice(0, 100))}${m.content.length > 100 ? "…" : ""}</div>
      </div>
    `).join("") : '<div class="empty-text">暂无记忆</div>';
    $("memoryTimeline").innerHTML = memHtml;

    const dreamHtml = dreams.length ? dreams.slice(0, 3).map(d => `
      <div class="dream-item">${escapeHtml(d.slice(0, 100))}${d.length > 100 ? "…" : ""}</div>
    `).join("") : '<div class="empty-text">暂无梦境记录</div>';
    $("dreamList").innerHTML = dreamHtml;

    $("actionOutput").innerHTML = "";
  } catch (e) {
    $("userDetailEmpty").style.display = "block";
    $("userDetail").style.display = "none";
    toast("加载用户详情失败：" + e.message, true);
  }
}

async function triggerAction(uid, action) {
  const out = $("actionOutput");
  out.textContent = "执行中…";
  try {
    const r = await apiPost("trigger", { uid, action });
    out.textContent = (r.result || "(空)").slice(0, 200);
    toast("已触发：" + action);
    if (action !== "growth") await loadUserDetail(uid);
  } catch (e) {
    out.textContent = "失败：" + e.message;
    toast(e.message, true);
  }
}

async function loadCharacters() {
  const sel = $("characterSwitchSelect");
  // 先把下拉框重置为明确占位，避免请求失败时永远卡在「加载中…」
  sel.innerHTML = '<option value="">选择角色...</option>';

  try {
    const uid = CURRENT_UID || (ALL_USERS.length ? ALL_USERS[0].uid : "");
    if (!uid) {
      $("characterGrid").innerHTML = '<div class="empty-text">请先选择用户</div>';
      $("collectionCount").textContent = "0/0";
      $("collectionProgress").style.width = "0%";
      return;
    }
    // bridge.apiGet 的第二个参数是 query params 对象，不要手工拼 URL
    const data = await apiGet("collection/all", { uid });
    const grid = $("characterGrid");
    const total = data.total_with_hidden || 7;
    const unlocked = data.unlocked_count + (data.hidden_unlocked ? 1 : 0);

    $("collectionCount").textContent = `${unlocked}/${total}`;
    $("collectionProgress").style.width = `${(unlocked / total) * 100}%`;

    grid.innerHTML = (data.characters || []).map(c => {
      if (!c.unlocked) {
        return `
      <div class="character-card locked">
        <div class="character-icon">${c.icon || "🎭"}</div>
        <div class="character-name">??? </div>
        <div class="character-desc">尚未解锁</div>
      </div>`;
      }
      const bg = c.background ? `<div class="character-bg">${escapeHtml(c.background)}</div>` : "";
      const phrases = (c.catchphrases || []).length
        ? `<div class="character-phrases">${(c.catchphrases || []).map(p => `<span class="phrase-chip">${escapeHtml(p)}</span>`).join("")}</div>`
        : "";
      const speak = c.speaking_style ? `<div class="character-speak">💬 ${escapeHtml(c.speaking_style)}</div>` : "";
      const verb = c.verbosity ? `<span class="char-meta">话痨度：${escapeHtml(c.verbosity)}</span>` : "";
      const emoji = c.emoji_usage ? `<span class="char-meta">表情：${escapeHtml(c.emoji_usage)}</span>` : "";
      return `
      <div class="character-card unlocked ${c.hidden ? "hidden" : ""}">
        <div class="character-icon">${c.icon || "🎭"}</div>
        <div class="character-name">${escapeHtml(c.name)}</div>
        <div class="character-desc">${escapeHtml(c.description)}</div>
        ${bg}${phrases}${speak}
        <div class="character-meta">${verb}${emoji}</div>
        <div class="character-progress"><div class="character-progress-fill" style="width:${c.affinity || 0}%"></div></div>
      </div>`;
    }).join("");

    // 更新角色切换下拉框（只列出该用户已解锁角色）
    const currentVal = sel.value;
    sel.innerHTML = '<option value="">选择角色...</option>' +
      (data.characters || []).filter(c => c.unlocked).map(c =>
        `<option value="${escapeHtml(c.id)}">${c.icon || "🎭"} ${escapeHtml(c.name)}</option>`
      ).join("");
    sel.value = currentVal;
  } catch (e) {
    $("characterGrid").innerHTML = '<div class="empty-text">加载失败</div>';
    sel.innerHTML = '<option value="">加载失败</option>';
    toast("角色加载失败：" + e.message, true);
  }
}

async function switchCharacter() {
  const sel = $("characterSwitchSelect");
  const charId = sel.value;
  if (!charId) {
    toast("请先选择一个角色", true);
    return;
  }
  try {
    // 使用当前选中的用户或默认使用第一个用户
    const uid = CURRENT_UID || (ALL_USERS.length ? ALL_USERS[0].uid : "");
    if (!uid) {
      toast("请先选择一个用户", true);
      return;
    }
    await apiPost("collection/switch", { uid, char_id: charId });
    toast("角色切换成功 ✨");
    await loadCharacters();
  } catch (e) {
    toast("切换失败：" + e.message, true);
  }
}

async function loadConfig() {
  try {
    // 缓存 busting：每次加载都带时间戳。注意 query 必须走 apiGet 第二参数
    // （params 对象），端点字符串里出现 `?` 会被宿主桥接 SDK 校验直接抛
    // "Plugin bridge endpoint is invalid"（请求不会发出）。
    const data = await apiGet("config", { v: Date.now() });
    CONFIG_DATA = data || {};

    document.querySelectorAll(".config-input[data-key]").forEach(input => {
      const key = input.dataset.key;
      const value = CONFIG_DATA[key];

      if (input.type === "checkbox") {
        input.checked = value === true;
      } else if (input.type === "range") {
        input.value = value ?? input.value;
        const display = document.querySelector(`.range-display[data-for="${key}"]`);
        if (display) {
          // 仅当滑块量程 ≤ 1（0~1 比例滑块）才按百分比显示；
          // llm_temperature 量程 0~2，必须显示原始数值，不能误显为百分比。
          const max = parseFloat(input.max);
          const isPercent = !isNaN(max) && max <= 1;
          display.textContent = (typeof value === "number" && isPercent)
            ? (value * 100).toFixed(0) + "%"
            : (typeof value === "number" ? value : input.value);
        }
      } else if (input.tagName.toLowerCase() === "textarea") {
        input.value = value ?? "";
      } else {
        // API Key 明文展示：直接回填真实值，所见即所得
        input.value = value ?? "";
      }
    });
    initExternalServiceToggles();
  } catch (e) {
    toast("加载配置失败：" + e.message, true);
  }
}

async function saveConfig() {
  try {
    // 保存前兜底补全：自定义模式卡片若已选模型但 key/base 仍空，用顶部底座补上，
    // 解决「早先已选模型、自动补全从未触发」导致 TTS/STT 等能力静默失效的问题。
    svcAutofillAll();
    const newConfig = {};
    // ① 收集面板上所有有 input 的 key（89 个可见配置项）
    document.querySelectorAll(".config-input[data-key]").forEach(input => {
      const key = input.dataset.key;
      if (input.type === "checkbox") {
        newConfig[key] = input.checked;
      } else if (input.type === "range") {
        newConfig[key] = parseFloat(input.value);
      } else if (input.type === "number") {
        newConfig[key] = parseInt(input.value) || 0;
      } else if (input.tagName.toLowerCase() === "textarea") {
        newConfig[key] = input.value.trim();
      } else {
        // API Key 明文展示：直接提交真实值，所见即所得（留空=清空）
        newConfig[key] = input.value;
      }
    });

    // ② 兜底补全：CONFIG_DATA 中还有不在面板上的 key（如 dream_api_key、
    // tts_long_text_threshold 等 28 个隐藏配置项），必须原样保留，
    // 否则后端 KV 覆盖层首次保存后会丢失这些键，导致后续配置不一致。
    if (CONFIG_DATA) {
      Object.keys(CONFIG_DATA).forEach(key => {
        if (!(key in newConfig)) {
          newConfig[key] = CONFIG_DATA[key];
        }
      });
    }

    // 渠道对齐（v4.8.115）：拉取模式=只选服务商，模型跟随服务商默认；
    // 拉取模式下旧 provider_<role>_model 残留若不清空，后端仍会按指定模型调用。
    // 显式清空，避免「拉取模式选了服务商却被旧模型配置劫持」。
    MC_PROVIDER_ROLES.forEach(r => {
      const panel = document.querySelector(`.mc-role-item[data-role="${r.key}"] .mc-mode-panel[data-mode="pull"]`);
      const visible = panel && panel.style.display !== "none";
      if (visible) {
        newConfig[`provider_${r.key}_model`] = "";
      }
    });

    await apiPost("config", newConfig);
    CONFIG_DATA = newConfig;
    toast("配置保存成功 ✨");
  } catch (e) {
    toast("保存失败：" + e.message, true);
  }
}

async function loadTokenStats() {
  try {
    const s = await apiGet("token_stats");
    if (s.error) {
      $("tokenSummary").innerHTML = '<div class="empty-text">加载失败</div>';
      $("taskChart").innerHTML = '<div class="empty-text">加载失败</div>';
      return;
    }

    const total = s.total || {};
    const today = s.today || {};

    $("tokenSummary").innerHTML = `
      <div class="token-stat">
        <div class="token-stat-value">${total.calls || 0}</div>
        <div class="token-stat-label">总调用次数</div>
      </div>
      <div class="token-stat">
        <div class="token-stat-value">${(total.prompt_tokens || 0) + (total.completion_tokens || 0)}</div>
        <div class="token-stat-label">总消耗 Tokens</div>
      </div>
      <div class="token-stat">
        <div class="token-stat-value">${today.calls || 0}</div>
        <div class="token-stat-label">今日调用</div>
      </div>
    `;

    const tasks = Object.entries(s.by_task || {});
    const maxCalls = Math.max(1, ...tasks.map(([_, d]) => d.calls || 0));

    $("taskChart").innerHTML = tasks.map(([task, d]) => {
      const percent = maxCalls > 0 ? (d.calls / maxCalls) * 100 : 0;
      return `
        <div class="task-row">
          <div class="task-name">${escapeHtml(task)}</div>
          <div class="task-bar-wrap">
            <div class="task-bar-fill" style="width:${percent}%"></div>
          </div>
          <div class="task-calls">${d.calls || 0}</div>
        </div>
      `;
    }).join("");
  } catch (e) {
    $("tokenSummary").innerHTML = '<div class="empty-text">加载失败</div>';
    $("taskChart").innerHTML = '<div class="empty-text">加载失败</div>';
  }
}

// ===== 对话检视 =====

function switchConvPanel(panelName) {
  document.querySelectorAll(".conv-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".conv-panel").forEach(p => p.classList.remove("active"));
  document.querySelector(`.conv-tab[data-conv="${panelName}"]`).classList.add("active");
  document.querySelector(`.conv-panel[data-conv-panel="${panelName}"]`).classList.add("active");

  if (panelName === "all") loadConversationList();
  if (panelName === "weighted") loadWeightedList();
  if (panelName === "learned") loadKnowledgeList();
  if (panelName === "stats") loadConversationStats();
}

async function loadConversationData() {
  // 加载对话检视需要的所有数据
  await loadConversationList();
}

async function loadConversationList() {
  try {
    const uid = $("convUserSelect").value;
    const msgType = $("convTypeSelect").value;
    const search = $("convSearch").value;
    const learnedOnly = $("convLearnedOnly").checked;

    const data = await apiGet("conversation/list", {
      uid, msg_type: msgType, search,
      learned_only: learnedOnly,
      limit: CONV_LIMIT, offset: CONV_OFFSET,
    });

    CONV_TOTAL = data.total || 0;
    renderConversationList(data.items || []);
    renderConversationPagination();
  } catch (e) {
    $("convList").innerHTML = `<div class="empty-text">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function renderConversationList(items) {
  const el = $("convList");
  if (!items.length) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">💬</div><p>暂无对话记录</p></div>';
    return;
  }
  el.innerHTML = items.map(it => `
    <div class="conv-item ${it.learned ? "learned" : ""}" data-id="${escapeHtml(it.id)}">
      <div class="conv-item-header">
        <div class="conv-avatar ${it.msg_type}">${it.msg_type === "bot" ? "🤖" : avatarText(it.sender_name, it.uid)}</div>
        <div class="conv-meta">
          <div class="conv-name">${it.msg_type === "bot" ? "Bot" : escapeHtml(it.sender_name || it.uid)}</div>
          <div class="conv-time">${formatTime(it.timestamp)}</div>
        </div>
        <div class="conv-badges">
          ${it.is_group ? '<span class="conv-badge group">群聊</span>' : ""}
          ${it.learned ? '<span class="conv-badge learned">已学</span>' : ""}
        </div>
      </div>
      <div class="conv-content">${escapeHtml(it.content)}</div>
      ${it.notes ? `<div class="conv-notes">📝 ${escapeHtml(it.notes)}</div>` : ""}
      ${it.tags?.length ? `<div class="conv-tags">${it.tags.map(t => `<span class="conv-tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
      <div class="conv-actions">
        ${!it.learned ? `<button class="conv-btn learn" data-act="learn">✨ 学习</button>` : `<button class="conv-btn" data-act="unlearn">取消学习</button>`}
        <button class="conv-btn delete" data-act="delete">删除</button>
      </div>
    </div>
  `).join("");

  el.querySelectorAll(".conv-btn[data-act='learn']").forEach(btn => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".conv-item");
      openLearnDialog(item.dataset.id);
    });
  });
  el.querySelectorAll(".conv-btn[data-act='unlearn']").forEach(btn => {
    btn.addEventListener("click", () => {
      const item = btn.closest(".conv-item");
      unlearnConversation(item.dataset.id);
    });
  });
  el.querySelectorAll(".conv-btn[data-act='delete']").forEach(btn => {
    btn.addEventListener("click", async () => {
      const item = btn.closest(".conv-item");
      if (await showConfirm("确定删除这条对话记录吗？")) {
        deleteConversation(item.dataset.id);
      }
    });
  });
}

function renderConversationPagination() {
  const totalPages = Math.ceil(CONV_TOTAL / CONV_LIMIT);
  const currentPage = Math.floor(CONV_OFFSET / CONV_LIMIT) + 1;
  const el = $("convPagination");
  if (totalPages <= 1) {
    el.innerHTML = "";
    return;
  }
  el.innerHTML = `
    <button class="conv-page-btn" ${currentPage <= 1 ? "disabled" : ""} id="convPrev">上一页</button>
    <span class="conv-page-info">${currentPage} / ${totalPages} (${CONV_TOTAL} 条)</span>
    <button class="conv-page-btn" ${currentPage >= totalPages ? "disabled" : ""} id="convNext">下一页</button>
  `;
  $("convPrev")?.addEventListener("click", () => {
    CONV_OFFSET = Math.max(0, CONV_OFFSET - CONV_LIMIT);
    loadConversationList();
  });
  $("convNext")?.addEventListener("click", () => {
    if (CONV_OFFSET + CONV_LIMIT < CONV_TOTAL) {
      CONV_OFFSET += CONV_LIMIT;
      loadConversationList();
    }
  });
}

function openLearnDialog(logId) {
  showPrompt("添加学习笔记", "笔记内容（可选）").then(notes => {
    if (notes === null) return;
    showPrompt("添加标签", "用逗号分隔，可选").then(tagsStr => {
      const tags = tagsStr ? tagsStr.split(",").map(t => t.trim()).filter(Boolean) : [];
      learnConversation(logId, notes, tags);
    });
  });
}

async function learnConversation(logId, notes, tags) {
  try {
    await apiPost("conversation/learn", { log_id: logId, notes, tags, importance: 0.7 });
    toast("已学习 ✨");
    loadConversationList();
  } catch (e) {
    toast("学习失败：" + e.message, true);
  }
}

async function unlearnConversation(logId) {
  try {
    await apiPost("conversation/unlearn", { log_id: logId });
    toast("已取消学习");
    loadConversationList();
  } catch (e) {
    toast("操作失败：" + e.message, true);
  }
}

async function deleteConversation(logId) {
  try {
    await apiPost("conversation/delete", { log_id: logId });
    toast("已删除");
    loadConversationList();
  } catch (e) {
    toast("删除失败：" + e.message, true);
  }
}

async function loadKnowledgeList() {
  try {
    const data = await apiGet("conversation/knowledge", { limit: 50 });
    const el = $("knowledgeList");
    if (!data.items || !data.items.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">📚</div><p>还没有学习的知识</p></div>';
      return;
    }
    el.innerHTML = data.items.map(it => `
      <div class="knowledge-item">
        <div class="knowledge-content">${escapeHtml(it.content)}</div>
        ${it.notes ? `<div class="knowledge-notes">📝 ${escapeHtml(it.notes)}</div>` : ""}
        <div class="knowledge-meta">
          <span>用户: ${escapeHtml(it.uid)}</span>
          <span>${formatTime(it.timestamp)}</span>
        </div>
      </div>
    `).join("");
  } catch (e) {
    $("knowledgeList").innerHTML = `<div class="empty-text">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

function updateConvWeightedUserSelect() {
  const sel = $("convWeightedUserSelect");
  const current = sel.value;
  sel.innerHTML = '<option value="">选择用户...</option>' +
    ALL_USERS.map(u => `<option value="${escapeHtml(u.uid)}">${escapeHtml(u.name || u.uid)}</option>`).join("");
  sel.value = current;
}

async function loadWeightedList() {
  updateConvWeightedUserSelect();
  const uid = $("convWeightedUserSelect").value;
  const el = $("convWeightedList");
  if (!uid) {
    el.innerHTML = '<div class="empty-state"><div class="empty-icon">⭐</div><p>选择用户查看高权重对话</p></div>';
    return;
  }
  try {
    const data = await apiGet("conversation/weighted", { uid, limit: 100 });
    if (!data.items || !data.items.length) {
      el.innerHTML = '<div class="empty-state"><div class="empty-icon">⭐</div><p>该用户暂无高权重对话（阈值 0.45）</p></div>';
      return;
    }
    el.innerHTML = data.items.map(it => {
      const w = (it.weight || 0) * 100;
      const factors = it.weight_factors || {};
      const tags = [];
      if ((factors.emotion || 0) > 0.15) tags.push(`情感 ${(factors.emotion * 100).toFixed(0)}%`);
      if ((factors.intimacy || 0) > 0.15) tags.push(`亲密 ${(factors.intimacy * 100).toFixed(0)}%`);
      if ((factors.self_disclosure || 0) > 0.15) tags.push(`自述 ${(factors.self_disclosure * 100).toFixed(0)}%`);
      if ((factors.important_topic || 0) > 0.15) tags.push(`重要 ${(factors.important_topic * 100).toFixed(0)}%`);
      return `
        <div class="conv-item ${it.extracted_to_memory ? "learned" : ""}">
          <div class="conv-item-header">
            <div class="conv-avatar ${it.msg_type}">${it.msg_type === "bot" ? "🤖" : "👤"}</div>
            <div class="conv-meta">
              <div class="conv-name">${escapeHtml(uid)}</div>
              <div class="conv-time">${formatTime(it.timestamp)}</div>
            </div>
            <div class="conv-badges">
              <span class="conv-badge group" style="background:rgba(251,191,36,0.15);color:var(--gold)">⭐ ${(it.weight || 0).toFixed(2)}</span>
              ${it.extracted_to_memory ? '<span class="conv-badge learned">已入记忆</span>' : ""}
            </div>
          </div>
          <div class="conv-content">${escapeHtml(it.content)}</div>
          <div class="weight-bar"><div class="weight-bar-fill" style="width:${w}%"></div></div>
          ${tags.length ? `<div class="weight-meta">${tags.map(t => `<span class="weight-tag">${escapeHtml(t)}</span>`).join("")}</div>` : ""}
        </div>
      `;
    }).join("");
  } catch (e) {
    el.innerHTML = `<div class="empty-text">加载失败：${escapeHtml(e.message)}</div>`;
  }
}

async function extractHighWeightToMemory() {
  const uid = $("convWeightedUserSelect").value;
  if (!uid) {
    toast("请先选择用户", true);
    return;
  }
  try {
    const r = await apiPost("conversation/extract", { uid, top_n: 10 });
    toast(`已抽取 ${r.extracted} 条高权重对话到记忆库 ✨`);
    await loadWeightedList();
    await loadConversationStats();
  } catch (e) {
    toast("抽取失败：" + e.message, true);
  }
}

async function reweightUser() {
  const uid = $("convWeightedUserSelect").value;
  if (!uid) {
    toast("请先选择用户", true);
    return;
  }
  try {
    const r = await apiPost("conversation/reweight", { uid });
    toast(`已重新计算 ${r.reweighted} 条对话权重`);
    await loadWeightedList();
  } catch (e) {
    toast("重算失败：" + e.message, true);
  }
}

async function loadConversationStats() {
  try {
    const data = await apiGet("conversation/stats");
    $("convStats").innerHTML = `
      <div class="conv-stat-card">
        <div class="conv-stat-value">${data.total_users || 0}</div>
        <div class="conv-stat-label">活跃用户</div>
      </div>
      <div class="conv-stat-card">
        <div class="conv-stat-value">${data.total_logs || 0}</div>
        <div class="conv-stat-label">对话记录</div>
      </div>
      <div class="conv-stat-card">
        <div class="conv-stat-value">${data.total_weighted || 0}</div>
        <div class="conv-stat-label">高权重对话</div>
      </div>
      <div class="conv-stat-card">
        <div class="conv-stat-value">${data.total_extracted || 0}</div>
        <div class="conv-stat-label">已入记忆</div>
      </div>
      <div class="conv-stat-card">
        <div class="conv-stat-value">${data.total_learned || 0}</div>
        <div class="conv-stat-label">已学知识</div>
      </div>
      <div class="conv-stat-card">
        <div class="conv-stat-value">${data.total_weighted > 0 ? ((data.total_extracted / data.total_weighted) * 100).toFixed(1) + "%" : "0%"}</div>
        <div class="conv-stat-label">入记率</div>
      </div>
    `;

    const users = data.user_stats || [];
    $("convUserRank").innerHTML = users.length ? users.map((u, i) => `
      <div class="conv-rank-item">
        <div class="conv-rank-index ${i < 3 ? "top" : ""}">${i + 1}</div>
        <div class="conv-rank-name">${escapeHtml(u.uid)}</div>
        <div class="conv-rank-count">${u.total} 条 · ⭐${u.weighted || 0} · 入记 ${u.extracted || 0}</div>
      </div>
    `).join("") : '<div class="empty-text">暂无数据</div>';
  } catch (e) {
    $("convStats").innerHTML = `<div class="empty-text">加载失败</div>`;
    $("convUserRank").innerHTML = `<div class="empty-text">加载失败</div>`;
  }
}

async function init() {
  // 检查 bridge 是否可用（脱离 AstrBot 面板时 window.AstrBotPluginPage 未注入）
  if (!bridge) {
    console.error("[mingbu] bridge is undefined! Cannot initialize admin panel.");
    // 即使 bridge 不可用，也要注册基础 UI 事件（标签切换、音色库按钮等）
    // 只有实际调用后端 API 的函数才需要 bridge
  } else {
    try {
      await bridge.ready();

      document.querySelectorAll(".tab").forEach(tab => {
        tab.addEventListener("click", () => switchTab(tab.dataset.tab));
      });

      $("saveConfig").addEventListener("click", saveConfig);

      $("fetchModelsBtn")?.addEventListener("click", fetchModels);

      $("mcRefetchProvidersBtn")?.addEventListener("click", mcRefetchProviders);

      document.querySelectorAll(".action-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          if (!CURRENT_UID) {
            toast("请先选择用户", true);
            return;
          }
          triggerAction(CURRENT_UID, btn.dataset.act);
        });
      });

      $("characterSwitchBtn")?.addEventListener("click", switchCharacter);

      document.querySelectorAll(".config-input.range").forEach(input => {
        input.addEventListener("input", () => {
          const display = document.querySelector(`.range-display[data-for="${input.dataset.key}"]`);
          if (display) {
            const val = parseFloat(input.value);
            const max = parseFloat(input.max);
            const isPercent = !isNaN(max) && max <= 1;
            display.textContent = isPercent ? (val * 100).toFixed(0) + "%" : val;
          }
        });
      });

      // 外部服务卡片：拉取 / 自定义 模式切换
      document.querySelectorAll(".svc-card").forEach(card => {
        card.querySelectorAll(".mode-btn").forEach(btn => {
          btn.addEventListener("click", () => svcSetMode(card, btn.dataset.mode, true));
        });
      });

      $("userSearch").addEventListener("input", (e) => {
        const query = e.target.value.toLowerCase();
        document.querySelectorAll(".user-item").forEach(item => {
          const name = item.querySelector(".user-name").textContent.toLowerCase();
          const uid = item.querySelector(".user-meta").textContent.toLowerCase();
          item.style.display = name.includes(query) || uid.includes(query) ? "" : "none";
        });
      });

      // 对话检视事件绑定
      document.querySelectorAll(".conv-tab").forEach(tab => {
        tab.addEventListener("click", () => switchConvPanel(tab.dataset.conv));
      });
      $("convRefresh").addEventListener("click", () => {
        const activePanel = document.querySelector(".conv-tab.active").dataset.conv;
        switchConvPanel(activePanel);
      });
      $("convUserSelect").addEventListener("change", () => { CONV_OFFSET = 0; loadConversationList(); });
      $("convTypeSelect").addEventListener("change", () => { CONV_OFFSET = 0; loadConversationList(); });
      $("convLearnedOnly").addEventListener("change", () => { CONV_OFFSET = 0; loadConversationList(); });
      $("convSearch").addEventListener("input", debounce(() => { CONV_OFFSET = 0; loadConversationList(); }, 500));
      $("convWeightedUserSelect").addEventListener("change", loadWeightedList);
      $("convExtractBtn").addEventListener("click", extractHighWeightToMemory);
      $("convReweightBtn").addEventListener("click", reweightUser);

      $("refreshDiagnostics")?.addEventListener("click", loadDiagnostics);
      document.querySelectorAll(".diagnostic-test").forEach(btn => {
        btn.addEventListener("click", () => runConnectivity(btn.dataset.service));
      });

      $("mingbuTestBtn")?.addEventListener("click", testMingbuBackend);

      // 四项首屏数据彼此独立，并发拉取以缩短白屏时间
      await Promise.all([
        loadStats(),
        loadUsers(),
        loadActivityFeed(),
        loadGraph(),
      ]);

      enhanceCombos(document);

      // 外部服务模型输入框：除 enhanceCombos 的 mousedown 选中外，也监听 change，
      // 确保键盘/datalist/直接输入等方式选择模型后都能触发自动补全。
      document.querySelectorAll(".svc-card input.config-input[data-key$='_model']").forEach(inp => {
        inp.addEventListener("change", () => svcAutofillOnModelSelect(inp));
      });

      // 预填服务商下拉数据源：即使未打开「模型统一配置」标签，渠道与服务配置里的服务商下拉也可用
      await mcLoadProviders();
      const svc = document.getElementById("svcProviderList");
      if (svc) svc.innerHTML = mcProviderOptionsHtml();

      // 为外部服务卡片注入「测试」按钮与状态区
      mcInitSvcTestButtons();

      setInterval(async () => {
        try { await loadStats(); } catch (_) {}
      }, 30000);

    } catch (e) {
      toast("初始化失败：" + e.message, true);
    }
  }

  // 音色库事件（v4.9.0：音色库已并入「核心配置」TTS 卡片，无独立 Tab；
  // 元素常驻 DOM，initTtsTab 直接注册即可）— 无论 bridge 是否可用都要注册，
  // 只有实际调用 API 的函数（loadTtsVoices / previewVoice）才需要 bridge
  initTtsTab();
}

function debounce(fn, ms) {
  let t;
  return (...args) => {
    clearTimeout(t);
    t = setTimeout(() => fn(...args), ms);
  };
}

// ── 自定义模态框：替代原生 confirm()/prompt()，视觉与面板统一 ──
function openModal({ title, bodyHtml, confirmText = "确定", cancelText = "取消",
                     showInput = false, inputPlaceholder = "", inputValue = "" }) {
  return new Promise((resolve) => {
    const root = $("modalRoot");
    if (!root) { resolve(showInput ? null : false); return; }
    const overlay = document.createElement("div");
    overlay.className = "modal-overlay";
    overlay.innerHTML = `
      <div class="modal-card" role="dialog" aria-modal="true">
        <div class="modal-title">${escapeHtml(title)}</div>
        ${bodyHtml ? `<div class="modal-body">${bodyHtml}</div>` : ""}
        ${showInput ? `<input type="text" class="modal-input" id="modalInput"
            placeholder="${escapeHtml(inputPlaceholder)}" value="${escapeHtml(inputValue)}" />` : ""}
        <div class="modal-actions">
          <button class="modal-btn cancel" id="modalCancel">${escapeHtml(cancelText)}</button>
          <button class="modal-btn confirm" id="modalOk">${escapeHtml(confirmText)}</button>
        </div>
      </div>`;
    root.appendChild(overlay);
    requestAnimationFrame(() => overlay.classList.add("show"));
    const input = overlay.querySelector("#modalInput");
    if (input) input.focus();

    const close = (val) => {
      overlay.classList.remove("show");
      setTimeout(() => overlay.remove(), 200);
      resolve(val);
    };
    overlay.querySelector("#modalCancel").addEventListener("click", () => close(showInput ? null : false));
    overlay.querySelector("#modalOk").addEventListener("click", () => close(showInput ? (input ? input.value : "") : true));
    overlay.addEventListener("click", (e) => { if (e.target === overlay) close(showInput ? null : false); });
    if (input) {
      input.addEventListener("keydown", (e) => { if (e.key === "Enter") overlay.querySelector("#modalOk").click(); });
    }
    const onEsc = (e) => {
      if (e.key === "Escape") { document.removeEventListener("keydown", onEsc); close(showInput ? null : false); }
    };
    document.addEventListener("keydown", onEsc);
  });
}

function showConfirm(message) {
  return openModal({
    title: "请确认",
    bodyHtml: `<p class="modal-msg">${escapeHtml(message)}</p>`,
  });
}

function showPrompt(title, placeholder) {
  return openModal({ title, showInput: true, inputPlaceholder: placeholder || "" });
}

// ===== 模型统一配置 =====

// 直连能力：填地址+Key 后从下拉选模型；prefix 对应 schema 里的 *_api_key/_api_base/_model
const MC_DIRECT_ROLES = [
  { key: "image_vision", label: "图片转述(视觉)", cats: ["vision"], prefix: "image_vision", enableKey: "image_vision_enabled" },
  { key: "embedding", label: "语义召回(Embedding)", cats: ["embedding"], prefix: "embedding", enableKey: "embedding_enabled" },
  { key: "rerank", label: "相关性重排(Rerank)", cats: ["rerank"], prefix: "rerank", enableKey: "rerank_enabled" },
  { key: "tts", label: "语音合成(TTS)", cats: ["tts"], prefix: "tts", enableKey: "tts_enabled" },
  { key: "stt", label: "语音识别(STT)", cats: ["stt"], prefix: "stt", enableKey: "stt_enabled" },
  { key: "merged_forward", label: "合并转发转述", cats: ["chat"], prefix: "merged_forward", enableKey: null },
];
// AstrBot 服务商：填 Provider ID（在 AstrBot 控制台配置），非模型名
const MC_PROVIDER_ROLES = [
  { key: "main_reply", label: "主回复" },
  { key: "dream", label: "梦境/预感" },
  { key: "growth", label: "成长反思" },
  { key: "proactive", label: "主动关怀" },
];
// 注：新闻整理 / 主动搜索目前只做网页抓取 + KV 存储，不调用 LLM 整理，
// 故没有可指定的「能力服务商」——其 provider_news/provider_search 等配置项永不消费，
// 已从面板移除，避免用户配置了却无效的失效槽位（schema 键保留以兼容存量配置）。

let MC_MODELS = [];
let MC_ASSIGN = {};        // 直连角色 key -> 模型 id
let MC_PROVIDER_VALS = {}; // provider 角色 key -> provider id
let MC_PROVIDER_MODELS = {}; // provider 角色 key -> 模型 id
let MC_PROVIDERS = [];      // AstrBot 已配置服务商 [{id,name}]
let MC_PROVIDER_DIAGNOSTICS = null; // 拉取诊断信息
let MC_PROVIDERS_LOADED = false;

async function mcLoadProviders() {
  // 拉一次 AstrBot 已配置服务商列表，供 Provider ID 框做下拉建议（可手输兜底）
  if (MC_PROVIDERS_LOADED) return MC_PROVIDERS;
  try {
    const r = await apiGet("list_providers");
    const list = (r && r.providers) || [];
    MC_PROVIDERS = list
      .map(p => (typeof p === "string" ? { id: p, name: p } : (p && p.id ? p : null)))
      .filter(Boolean);
    MC_PROVIDER_DIAGNOSTICS = (r && r.diagnostics) || null;
  } catch (e) {
    MC_PROVIDERS = [];   // 取不到就降级：纯文本框手输
    MC_PROVIDER_DIAGNOSTICS = {
      root_cause: "enumeration_failed",
      message: `请求失败：${e.message || "未知错误"}`,
      strategies: [],
    };
  }
  MC_PROVIDERS_LOADED = true;
  return MC_PROVIDERS;
}

async function mcRefetchProviders() {
  // 强制忽略缓存，重新从 AstrBot 拉取服务商列表并重渲能力服务商卡片。
  // 「拉取模式」仅在页面打开时自动触发一次；若彼时 AstrBot 尚未就绪，
  // 下拉会空掉且无可重试入口。此按钮提供手动重试，避免重启插件。
  const btn = $("mcRefetchProvidersBtn");
  if (btn) { btn.disabled = true; btn.textContent = "🔄 拉取中…"; }
  try {
    MC_PROVIDERS_LOADED = false;
    MC_PROVIDERS = [];
    MC_PROVIDER_DIAGNOSTICS = null;
    await renderMcRoles();
    const ok = MC_PROVIDERS && MC_PROVIDERS.length > 0;
    toast(ok
      ? `已拉取 ${MC_PROVIDERS.length} 个服务商`
      : (MC_PROVIDER_DIAGNOSTICS && MC_PROVIDER_DIAGNOSTICS.message
          ? `拉取为空：${MC_PROVIDER_DIAGNOSTICS.message}` : "未拉取到服务商"));
  } catch (e) {
    toast(`拉取服务商失败：${e.message || "未知错误"}`, true);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "🔄 重新拉取服务商"; }
  }
}

function mcProviderDiagnosticsHtml() {
  if (!MC_PROVIDER_DIAGNOSTICS) return "";
  const d = MC_PROVIDER_DIAGNOSTICS;
  const causeMap = {
    none: ["success", "已拉取到服务商列表"],
    context_missing: ["error", d.message],
    manager_missing: ["error", d.message],
    id_attribute_missing: ["warning", d.message],
    enumeration_failed: ["error", d.message],
    no_providers_configured: ["info", d.message],
  };
  const [cls, text] = causeMap[d.root_cause] || ["info", d.message || "未知状态"];
  let html = `<div class="mc-diag mc-diag-${cls}">${escapeHtml(text)}</div>`;
  if (d.strategies && d.strategies.length) {
    const rows = d.strategies.map(s => {
      const err = s.error ? ` <span class="mc-diag-err">${escapeHtml(s.error)}</span>` : "";
      return `<div class="mc-diag-row"><span class="mc-diag-name">${escapeHtml(s.name)}</span><span class="mc-diag-count">${s.found}</span>${err}</div>`;
    }).join("");
    html += `<details class="mc-diag-details"><summary>枚举详情</summary>${rows}</details>`;
  }
  return html;
}

function mcSetTestStatus(sel, ok, text) {
  const el = typeof sel === "string" ? document.querySelector(sel) : sel;
  if (!el) return;
  el.className = "mc-test-status " + (ok ? "ok" : "err");
  el.innerHTML = escapeHtml(text);
}

async function mcTestProviderRole(role) {
  const item = document.querySelector(`.mc-role-item[data-role="${role}"]`);
  const statusSel = `.mc-role-item[data-role="${role}"] .mc-test-status[data-kind="provider"]`;
  if (!item) return;
  // 当前激活模式：custom 面板可见则按自定义直连测，否则按拉取(Provider)测
  const customPanel = item.querySelector('.mc-mode-panel[data-mode="custom"]');
  const isCustom = customPanel && customPanel.style.display !== "none";
  const providerId = (item.querySelector(".mc-provider-input")?.value || "").trim();
  const apiKey = (item.querySelector(`.config-input[data-key="${role}_api_key"]`)?.value || "").trim();
  const apiBase = (item.querySelector(`.config-input[data-key="${role}_api_base"]`)?.value || "").trim();
  const dModel = (item.querySelector(`.config-input[data-key="${role}_model"]`)?.value || "").trim();
  mcSetTestStatus(statusSel, true, "测试中…");
  try {
    if (isCustom) {
      if (!apiKey || !apiBase) {
        mcSetTestStatus(statusSel, false, "请先填写 API Key 与接口地址（自定义模式）");
        return;
      }
      const r = await apiPost("connectivity", {
        service: "provider", provider_id: "", task_type: role,
        api_key: apiKey, api_base: apiBase, model: dModel,
      });
      if (r && r.ok) {
        mcSetTestStatus(statusSel, true, `直连连通 · ${r.latency_ms}ms · ${(r.response_preview || "").slice(0, 40)}`);
      } else {
        mcSetTestStatus(statusSel, false, `失败：${r && r.error ? r.error : "未知错误"}`);
      }
      return;
    }
    // 拉取模式：直接测面板里的服务商 ID（不依赖已保存配置）
    if (!providerId) {
      mcSetTestStatus(statusSel, false, "请先填写或选择服务商 ID（拉取模式）");
      return;
    }
    const payload = { service: "provider", provider_id: providerId };
    const r = await apiPost("connectivity", payload);
    if (r && r.ok) {
      const detail = r.resolved_only
        ? `${r.provider_id || providerId || "Provider"} 已解析（不支持 text_chat，仅确认 ID）`
        : `${r.provider_id || providerId || "Provider"} 连通 · ${r.latency_ms}ms · ${(r.response_preview || "").slice(0, 40)}`;
      mcSetTestStatus(statusSel, true, detail);
    } else {
      mcSetTestStatus(statusSel, false, `失败：${r && r.error ? r.error : "未知错误"}`);
    }
  } catch (e) {
    mcSetTestStatus(statusSel, false, `请求异常：${e.message}`);
  }
}

async function mcTestSvcCard(card, mode) {
  const svc = card.dataset.svc;
  const providerKey = card.dataset.providerKey;
  const customKeys = (card.dataset.customKeys || "").split(",");
  const statusEl = card.querySelector(".svc-test-status");
  if (statusEl) {
    statusEl.className = "svc-test-status";
    statusEl.textContent = "测试中…";
  }
  try {
    if (mode === "pull") {
      const providerId = (card.querySelector(`[data-key="${providerKey}"]`)?.value || "").trim();
      if (!providerId) {
        if (statusEl) { statusEl.className = "svc-test-status err"; statusEl.textContent = "请先填写或选择服务商 ID"; }
        return;
      }
      const r = await apiPost("connectivity", { service: "provider", provider_id: providerId });
      if (statusEl) {
        statusEl.className = r && r.ok ? "svc-test-status ok" : "svc-test-status err";
        statusEl.textContent = r && r.ok
          ? `${providerId} 已解析 · ${r.latency_ms}ms${r.resolved_only ? "（仅确认 ID）" : ""}`
          : `失败：${r && r.error ? r.error : "未知错误"}`;
      }
    } else {
      const apiKey = (card.querySelector(`[data-key="${customKeys[0]}"]`)?.value || "").trim();
      const apiBase = (card.querySelector(`[data-key="${customKeys[1]}"]`)?.value || "").trim();
      const model = (card.querySelector(`[data-key="${customKeys[2]}"]`)?.value || "").trim();
      if (!apiKey || !apiBase) {
        if (statusEl) { statusEl.className = "svc-test-status err"; statusEl.textContent = "请先填写 API Key 与接口地址"; }
        return;
      }
      if (!["tts", "stt", "embedding", "rerank", "vision", "merged", "interpret"].includes(svc)) {
        if (statusEl) { statusEl.className = "svc-test-status err"; statusEl.textContent = "该服务暂不支持自定义模式连通性测试"; }
        return;
      }
      // DashScope Qwen TTS 不再做前端拦截：后端 diagnostics 对 qwen-tts/
      // qwen3-tts 模型走 MultiModalConversation SDK 真实合成测试（0393d33+），
      // 直接由后端返回真实连通结果（含合成字节数）。
      const r = await apiPost("connectivity", { service: svc, api_key: apiKey, api_base: apiBase, model });
      if (statusEl) {
        statusEl.className = r && r.ok ? "svc-test-status ok" : "svc-test-status err";
        statusEl.textContent = r && r.ok
          ? `连通 · ${r.latency_ms}ms${r.response_preview ? " · " + r.response_preview.slice(0, 40) : ""}`
          : `失败：${r && r.error ? r.error : "未知错误"}`;
      }
    }
  } catch (e) {
    if (statusEl) { statusEl.className = "svc-test-status err"; statusEl.textContent = `请求异常：${e.message}`; }
  }
}

function mcInitSvcTestButtons() {
  document.querySelectorAll(".svc-card").forEach(card => {
    if (card.querySelector(".btn-test-svc")) return; // 已注入
    const head = card.querySelector(".svc-card-head");
    if (!head) return;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn-test-svc";
    btn.textContent = "测试";
    btn.title = "测试当前配置（拉取/自定义）的连通性";
    head.appendChild(btn);
    const status = document.createElement("div");
    status.className = "svc-test-status";
    card.insertBefore(status, card.querySelector(".svc-mode") || head.nextSibling);
    btn.addEventListener("click", () => {
      const mode = card.classList.contains("mode-custom") ? "custom" : "pull";
      mcTestSvcCard(card, mode);
    });
  });
}

function mcPrefillEndpoint() {
  // 端点预填：优先使用已保存的 mc_base_url；否则从其它直连能力的 api_base
  // 里取一个端点做便利预填（省去重复输入），且绝不覆盖已保存值。
  if (CONFIG_DATA.mc_base_url) {
    $("mcBaseUrl").value = CONFIG_DATA.mc_base_url;
    return;
  }
  const order = ["embedding", "tts", "rerank", "stt", "image_vision", "merged_forward"];
  for (const r of order) {
    const b = CONFIG_DATA[`${r}_api_base`] || "";
    if (b) {
      $("mcBaseUrl").value = b;
      break;
    }
  }
}

// ===== 自定义下拉（修复 input[list] 箭头不可点击；支持从已拉取模型列表选择） =====
// 触发机制：点击输入框或右侧箭头按钮打开弹出列表（fixed 定位，避免被父容器 overflow 裁剪）。
// 数据绑定：provider 类从关联 <datalist> 读取；带 data-cats 的模型类从已拉取的 MC_MODELS 按能力过滤读取。
// 导入后状态更新：选中即写入 input.value 并派发 input 事件 → 由 data-key 经 saveConfig 落入配置；
//               同时同步 MC_ASSIGN 供「拉取的模型」目录徽标刷新。
let _comboPop = null;
let _comboInput = null;

function mcRenderModelDatalist() {
  const dl = document.getElementById("mcModelList");
  if (dl) dl.innerHTML = MC_MODELS.map(m => `<option value="${escapeHtml(m.id)}"></option>`).join("");
}

function mcProviderOptionsHtml() {
  return MC_PROVIDERS.map(p => {
    const pname = (p.name && p.name !== p.id) ? `${escapeHtml(p.name)} (${escapeHtml(p.id)})` : escapeHtml(p.id);
    return `<option value="${escapeHtml(p.id)}">${pname}</option>`;
  }).join("");
}

function _ensureComboPop() {
  if (_comboPop) return _comboPop;
  _comboPop = document.createElement("ul");
  _comboPop.className = "combo-pop";
  _comboPop.setAttribute("role", "listbox");
  _comboPop.hidden = true;
  document.body.appendChild(_comboPop);
  // 滚轮在列表展开状态下：阻止冒泡 + 禁用页面滚动联动，使列表保持展开
  _comboPop.addEventListener("wheel", _onComboWheel, { passive: false });
  document.addEventListener("click", (e) => {
    if (!_comboPop || _comboPop.hidden) return;
    if (e.target === _comboInput) return;
    if (_comboInput && _comboInput.contains(e.target)) return;
    if (e.target.closest && e.target.closest(".combo-arrow")) return;
    if (_comboPop.contains(e.target)) return;
    _closeCombo();
  });
  // 滚动不再关闭下拉（避免滚轮误收起）；页面滚动时仅重定位贴合输入框
  window.addEventListener("scroll", _onComboScroll, true);
  window.addEventListener("resize", _closeCombo);
  window.addEventListener("blur", _closeCombo);
  return _comboPop;
}

function _comboOptions(input) {
  const cats = (input.dataset.cats || "").split(",").map(s => s.trim()).filter(Boolean);
  if (cats.length) {
    return MC_MODELS
      .filter(m => m.cats && m.cats.some(c => cats.includes(c)))
      .map(m => ({ value: m.id, label: m.id, sub: mcPriceText(m) }));
  }
  const dlId = input.getAttribute("list");
  const dl = dlId && document.getElementById(dlId);
  if (dl) {
    return Array.from(dl.querySelectorAll("option")).map(o => ({ value: o.value, label: o.textContent || o.value }));
  }
  return [];
}

function _isModelCombo(input) {
  const cats = (input.dataset.cats || "").split(",").map(s => s.trim()).filter(Boolean);
  return cats.length > 0;
}

function _comboEmptyText(input) {
  if (_isModelCombo(input)) {
    if (!MC_MODELS.length) return "（模型列表为空，请先点击「拉取模型列表」）";
    return "（暂无匹配模型，可直接手输；或先「拉取模型列表」获取更多模型）";
  }
  // provider / 其它 datalist 类
  if (!MC_PROVIDERS_LOADED) return "（正在加载服务商列表...）";
  if (!MC_PROVIDERS.length) return "（当前 AstrBot 未配置任何服务商，请到「设置 → 模型服务」添加后再选，或直接手输 ID）";
  return "（无匹配的服务商，可直接手输 ID）";
}

function _refreshProviderDatalists() {
  const html = mcProviderOptionsHtml();
  const mcDl = document.getElementById("mcProviderList");
  if (mcDl) mcDl.innerHTML = html;
  const svcDl = document.getElementById("svcProviderList");
  if (svcDl) svcDl.innerHTML = html;
}

function _positionComboPop(input, resetScroll = true) {
  const pop = _comboPop;
  const rect = input.getBoundingClientRect();
  pop.style.position = "fixed";
  pop.style.top = (rect.bottom + 4) + "px";
  pop.style.left = rect.left + "px";
  pop.style.width = Math.max(rect.width, 200) + "px";
  if (resetScroll) pop.scrollTop = 0;
}

function _renderComboList(input, opts) {
  const pop = _comboPop;
  const q = input.value.trim().toLowerCase();
  const list = q ? opts.filter(o => (o.value.toLowerCase().includes(q)) || (o.label || "").toLowerCase().includes(q)) : opts;
  if (!list.length) {
    pop.innerHTML = `<li class="combo-empty">${_comboEmptyText(input)}</li>`;
    return;
  }
  pop.innerHTML = list.map(o => {
    const sel = o.value === input.value ? " selected" : "";
    const sub = o.sub ? `<span class="combo-sub">${escapeHtml(o.sub)}</span>` : "";
    return `<li class="combo-opt${sel}" data-value="${escapeHtml(o.value)}">${escapeHtml(o.label)}${sub}</li>`;
  }).join("");
  pop.querySelectorAll(".combo-opt").forEach(li => {
    li.addEventListener("mousedown", (e) => { e.preventDefault(); _selectCombo(input, li.dataset.value); });
  });
}

async function _openCombo(input) {
  const pop = _ensureComboPop();
  _comboInput = input;

  // provider 类：打开前确保 AstrBot 已配置服务商列表已加载，避免空态提示误导
  if (!_isModelCombo(input) && !MC_PROVIDERS_LOADED) {
    pop.innerHTML = `<li class="combo-empty">${_comboEmptyText(input)}</li>`;
    pop.hidden = false;
    _positionComboPop(input);
    await mcLoadProviders();
    _refreshProviderDatalists();
  }

  _renderComboList(input, _comboOptions(input));
  _positionComboPop(input);
  pop.hidden = false;
}

function _closeCombo() {
  if (_comboPop) _comboPop.hidden = true;
  _comboInput = null;
}

// 列表展开时滚动不再关闭下拉；仅在「页面滚动」时重定位面板以贴合输入框。
// 若是列表自身滚动（combo-pop 的 scroll），既不重定位也不关闭。
function _onComboScroll(e) {
  if (!_comboPop || _comboPop.hidden || !_comboInput) return;
  if (e.target === _comboPop) return;
  _positionComboPop(_comboInput, false);
}

// 列表展开时，面板内的鼠标滚轮：保持列表展开，仅在到达列表边界后才放行页面滚动。
// - 阻止滚轮冒泡，避免误触发 window 的滚动/全局监听；
// - 面板可滚动时在其内部滚动并禁用页面滚动联动；面板不可滚动时彻底屏蔽页面滚动。
function _onComboWheel(e) {
  if (!_comboPop || _comboPop.hidden) return;
  if (!_comboPop.contains(e.target)) return; // 仅处理面板内部的滚轮
  e.stopPropagation(); // 阻止冒泡到 window 的滚动/全局监听，避免意外收起
  const pop = _comboPop;
  if (pop.scrollHeight <= pop.clientHeight) {
    e.preventDefault(); // 面板不可滚动：完全禁用页面滚动联动
    return;
  }
  const atTop = pop.scrollTop <= 0;
  const atBottom = pop.scrollTop + pop.clientHeight >= pop.scrollHeight - 1;
  const delta = e.deltaY || 0;
  // 未到达列表边界：放行浏览器默认行为——面板自身内部滚动，保证滚轮可用
  if ((delta < 0 && atTop) || (delta > 0 && atBottom)) {
    e.preventDefault(); // 到达边界：阻止滚动链继续联动到页面（不滚动面板）
  }
}

function _selectCombo(input, value) {
  input.value = value;
  input.dispatchEvent(new Event("input", { bubbles: true }));
  if (input.dataset.role && input.dataset.cats) {
    MC_ASSIGN[input.dataset.role] = value;
  }
  // 外部服务卡片：自定义模式下选中模型后，自动从「已拉取底座」补全 API Key 与接口地址
  if (input.closest && input.closest(".svc-card")) {
    svcAutofillOnModelSelect(input);
  }
  // 能力服务商自定义模式：选中模型后自动补全同卡片的 Key/Base
  if (input.closest && input.closest(".mc-role-item")) {
    mcAutofillCustom(input);
  }
  _closeCombo();
}

// 外部服务卡片：自定义模式下选中模型后，自动从「已拉取底座」补全 API Key 与接口地址。
// 后端把 api_base 当作 base（到 /v1 为止）并自行拼接 /audio/speech 等子路径，故此处只填 base，
// 不拼子路径，避免双重拼接（如 …/v1/audio/speech/audio/speech）。仅当字段为空时补全，不覆盖手填。
// 外部服务卡片：自定义模式下，从「已拉取底座」补全 API Key 与接口地址。
// 后端把 api_base 当作 base（到 /v1 为止）并自行拼接 /audio/speech 等子路径，故此处只填 base，
// 不拼子路径，避免双重拼接。仅当字段为空时补全，不覆盖手填。
// opts.silent=true（如保存时）不弹提示。
function svcAutofillCard(card, opts = {}) {
  const silent = !!opts.silent;
  if (!card || !card.classList.contains("mode-custom")) return;
  // 从卡片 data-custom-keys 解析真实字段名——前缀不一定是 svc 名
  // （vision→image_vision_*、merged→merged_forward_*、interpret→mingbu_interpret_*）；
  // 直接拼 ${svc}_* 会查不到而静默失效，导致该卡自动补全完全不工作。
  const custom = (card.dataset.customKeys || "").split(",").map(s => s.trim()).filter(Boolean);
  const keyKey = custom.find(k => k.endsWith("_api_key")) || "";
  const baseKey = custom.find(k => k.endsWith("_api_base") || k.endsWith("_base_url")) || "";
  if (!keyKey || !baseKey) return;
  const keyInput = document.querySelector(`.config-input[data-key="${keyKey}"]`);
  const baseEl = document.querySelector(`.config-input[data-key="${baseKey}"]`);
  if (!keyInput || !baseEl) return;
  const base = (($("mcBaseUrl") || {}).value || "").trim();
  const key = (($("mcApiKey") || {}).value || "").trim();
  const needKey = !keyInput.value.trim();
  const needBase = !baseEl.value.trim();
  if (!needKey && !needBase) return; // 已手填，不覆盖
  if ((!base && needBase) || (!key && needKey)) {
    if (!silent) toast("请先填写顶部「模型统一配置」的接口地址与 API Key，再选模型即可自动补全", true);
    return;
  }
  if (needKey && key) {
    keyInput.value = key;
    keyInput.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (needBase && base) {
    baseEl.value = normalizeBaseToV1(base);
    baseEl.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

// 外部服务卡片：自定义模式下选中模型后触发（保留原即时补全行为）
function svcAutofillOnModelSelect(input) {
  const card = input.closest && input.closest(".svc-card");
  svcAutofillCard(card);
}

// 能力服务商自定义模式：选中模型后，从顶部「模型统一配置」自动补全 Key/Base。
// 与外部服务 svcAutofillCard 同款行为——仅当字段为空时补全，不覆盖手填。
// 输入框 data-key 形如 {role}_model，据此推导 role，再定位同卡片三个字段。
function mcAutofillCustom(input) {
  const dk = input && input.dataset && input.dataset.key;
  if (!dk || !dk.endsWith("_model")) return;
  const role = dk.replace(/_model$/, "");
  const item = input.closest && input.closest(".mc-role-item");
  if (!item) return;
  const keyInput = item.querySelector(`.config-input[data-key="${role}_api_key"]`);
  const baseInput = item.querySelector(`.config-input[data-key="${role}_api_base"]`);
  if (!keyInput || !baseInput) return;
  const base = (($("mcBaseUrl") || {}).value || "").trim();
  const key = (($("mcApiKey") || {}).value || "").trim();
  const needKey = !keyInput.value.trim();
  const needBase = !baseInput.value.trim();
  if (!needKey && !needBase) return; // 已手填，不覆盖
  if ((!base && needBase) || (!key && needKey)) {
    toast("请先填写顶部「模型统一配置」的接口地址与 API Key，再选模型即可自动补全", true);
    return;
  }
  if (needKey && key) {
    keyInput.value = key;
    keyInput.dispatchEvent(new Event("input", { bubbles: true }));
  }
  if (needBase && base) {
    baseInput.value = normalizeBaseToV1(base);
    baseInput.dispatchEvent(new Event("input", { bubbles: true }));
  }
}

// 保存兜底：遍历全部自定义模式卡片，把仍空的 key/base 用顶部底座补上，
// 解决「早先已选模型、但 key/base 从未被补全」的遗留空状态。
function svcAutofillAll() {
  document.querySelectorAll(".svc-card.mode-custom").forEach(card => svcAutofillCard(card, { silent: true }));
}

function normalizeBaseToV1(b) {
  b = (b || "").trim().replace(/\/+$/, "").replace(/\/v1$/, "");
  return b + "/v1";
}

async function _comboKey(input, e) {
  if (_comboPop.hidden) {
    if (e.key === "ArrowDown" || e.key === "Enter") { e.preventDefault(); await _openCombo(input); }
    return;
  }
  const items = Array.from(_comboPop.querySelectorAll(".combo-opt"));
  if (!items.length) { if (e.key === "Escape") _closeCombo(); return; }
  let cur = items.findIndex(li => li.classList.contains("active"));
  if (e.key === "ArrowDown") {
    e.preventDefault();
    cur = (cur + 1) % items.length;
  } else if (e.key === "ArrowUp") {
    e.preventDefault();
    cur = (cur - 1 + items.length) % items.length;
  } else if (e.key === "Enter") {
    e.preventDefault();
    _selectCombo(input, items[cur >= 0 ? cur : 0].dataset.value);
    return;
  } else if (e.key === "Escape") {
    e.preventDefault(); _closeCombo(); return;
  } else { return; }
  items.forEach(x => x.classList.remove("active"));
  items[cur].classList.add("active");
  items[cur].scrollIntoView({ block: "nearest" });
}

function enhanceCombos(root) {
  const inputs = root.querySelectorAll("input.config-input[list]:not([data-combo]), input.config-input[data-cats]:not([data-combo])");
  inputs.forEach(input => {
    input.dataset.combo = "1";
    const wrap = document.createElement("span");
    wrap.className = "combo";
    input.parentNode.insertBefore(wrap, input);
    wrap.appendChild(input);
    const arrow = document.createElement("button");
    arrow.type = "button";
    arrow.className = "combo-arrow";
    arrow.setAttribute("aria-label", "展开选项列表");
    arrow.innerHTML = '<svg viewBox="0 0 12 12" width="12" height="12"><path d="M2 4l4 4 4-4" stroke="currentColor" stroke-width="1.6" fill="none" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    wrap.appendChild(arrow);
    arrow.addEventListener("click", async (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (_comboInput === input && !_comboPop.hidden) _closeCombo();
      else await _openCombo(input);
    });
    input.addEventListener("click", async () => { if (_comboInput !== input || _comboPop.hidden) await _openCombo(input); });
    input.addEventListener("input", () => { if (_comboInput === input && !_comboPop.hidden) _renderComboList(input, _comboOptions(input)); });
    input.addEventListener("keydown", async (e) => _comboKey(input, e));
  });
}

async function loadModelsTab() {
  try {
    if (!Object.keys(CONFIG_DATA).length) {
      const data = await apiGet("config");
      CONFIG_DATA = data || {};
    }
    mcPrefillEndpoint();
    MC_ASSIGN = {};
    MC_PROVIDER_VALS = {};
    MC_PROVIDER_MODELS = {};
    for (const r of MC_DIRECT_ROLES) {
      const m = CONFIG_DATA[`${r.prefix}_model`] || "";
      if (m) MC_ASSIGN[r.key] = m;
    }
    for (const r of MC_PROVIDER_ROLES) {
      MC_PROVIDER_VALS[r.key] = CONFIG_DATA[`provider_${r.key}`] || "";
      const pm = CONFIG_DATA[`provider_${r.key}_model`] || "";
      if (pm) MC_PROVIDER_MODELS[r.key] = pm;
    }
    // 恢复此前持久化的模型目录（拉取后已落盘），避免「再次进入全部消失」。
    // 端点凭据（mc_base_url / mc_api_key / mc_extra_headers）已由 loadConfig 经
    // data-key 从配置恢复；这里仅补模型列表，以及历史目录里残留的 base/headers 兜底。
    let restored = 0;
    try {
      const saved = await apiGet("model_catalog_get");
      if (saved && saved.ok && saved.catalog && Array.isArray(saved.catalog.models) && saved.catalog.models.length) {
        MC_MODELS = saved.catalog.models;
        restored = MC_MODELS.length;
        if (saved.catalog.base_url && !CONFIG_DATA.mc_base_url) $("mcBaseUrl").value = saved.catalog.base_url;
        if (saved.catalog.extra_headers && !CONFIG_DATA.mc_extra_headers) {
          $("mcExtraHeaders").value = (typeof saved.catalog.extra_headers === "string")
            ? saved.catalog.extra_headers
            : JSON.stringify(saved.catalog.extra_headers);
        }
      }
    } catch (e) {
      // 恢复失败不影响主流程：用户可重新拉取
    }
    // 直接渲染（不再等拉取）：三种方式（拉取/手输/默认）合并在同一框架，始终可见
    $("mcStatus").textContent = restored
      ? `已恢复上次拉取的 ${restored} 个模型；可重新「拉取模型列表」刷新，或先为各能力选/填模型。下方外部服务留空即用 AstrBot 默认。`
      : "可直接为各能力选/填模型，或先填端点「拉取模型列表」再选；下方外部服务留空即用 AstrBot 默认。";
    await renderMcRoles();
    renderMcCatalog();
  } catch (e) {
    toast("加载模型配置失败：" + e.message, true);
  }
}

async function fetchModels() {
  const base = $("mcBaseUrl").value.trim();
  const key = $("mcApiKey").value.trim();
  const headers = $("mcExtraHeaders").value.trim();
  if (!base) {
    toast("请先填写接口地址 (Base URL)", true);
    return;
  }
  $("mcStatus").textContent = "拉取中…";
  try {
    const r = await apiPost("model_catalog", { base_url: base, api_key: key, extra_headers: headers });
    MC_MODELS = r.models || [];
    if (!MC_MODELS.length) {
      $("mcStatus").textContent = "该端点未返回任何模型（可能不支持 /models 或 Key 无权限）。各能力仍可手输模型名。";
      await renderMcRoles();
      renderMcCatalog();
      return;
    }
    $("mcStatus").textContent = `已拉取 ${MC_MODELS.length} 个模型，为各能力选择对应模型后点「保存全部」。`;
    await renderMcRoles();
    renderMcCatalog();
    // 拉取成功后立即持久化模型目录 + 端点，确保再次进入页面不丢失
    try {
      const res = await apiPost("model_catalog_save", {
        base_url: base,
        api_key: key,
        extra_headers: headers,
        models: MC_MODELS,
      });
      if (!res || !res.ok) {
        toast("模型目录保存失败（不影响本次使用）：" + ((res && res.message) || "未知错误"), true);
      }
    } catch (e) {
      toast("模型目录保存失败（不影响本次使用）：" + e.message, true);
    }
  } catch (e) {
    $("mcStatus").textContent = "拉取失败：" + e.message;
    toast("拉取失败：" + e.message, true);
  }
}

async function renderMcRoles() {
  const providers = await mcLoadProviders();
  const providerOptions = mcProviderOptionsHtml();
  const diagnosticsHtml = mcProviderDiagnosticsHtml();

  let html = `<datalist id="mcProviderList">${providerOptions}</datalist>`;
  if (diagnosticsHtml) {
    html += `<div class="mc-diag-box">${diagnosticsHtml}</div>`;
  }

  // 外部服务卡片（下方「外部服务」区）复用同一份服务商建议
  const svcDl = document.getElementById("svcProviderList");
  if (svcDl) svcDl.innerHTML = providerOptions;

  // ── 能力服务商：双模式（借鉴「外部服务」方案，两个渠道互斥）──
  //  🔗拉取模式：只选 AstrBot 已配置服务商（或手输 ID），留空＝AstrBot 默认；
  //              模型跟随该服务商默认，不再单独选模型（对齐外部服务框架）。
  //  ✏️自定义：独立直连 key/base/model，不依赖 AstrBot 服务商；
  //            模型从顶部「模型统一配置」区拉取的列表中选择。
  //            「服务商」与「模型」彻底解耦（历史坑：服务商空+模型填非默认服务商→400）。
  for (const r of MC_PROVIDER_ROLES) {
    const cur = MC_PROVIDER_VALS[r.key] || "";
    const curModel = MC_PROVIDER_MODELS[r.key] || ""; // 兼容旧配置残留，仅在自定义模式直连时兜底使用
    const curKey = CONFIG_DATA[`${r.key}_api_key`] || "";
    const curBase = CONFIG_DATA[`${r.key}_api_base`] || "";
    const curDModel = CONFIG_DATA[`${r.key}_model`] || "";
    // 模式状态持久化（v4.8.112）：localStorage 记忆优先；首次进入（无记忆）时
    // 若已填自定义 Key+Base 则智能切到自定义，否则默认拉取——刷新不再丢状态。
    let activeMode = "pull";
    try {
      const savedMode = localStorage.getItem(`mingbu.mcMode.${r.key}`);
      if (savedMode === "custom" || savedMode === "pull") {
        activeMode = savedMode;
      } else {
        activeMode = (curKey && curBase) ? "custom" : "pull";
      }
    } catch (e) {
      activeMode = (curKey && curBase) ? "custom" : "pull";
    }
    const hint = providers.length
      ? `从下拉选择 AstrBot「<code>设置 → 模型服务</code>」里已配置的服务商（也可直接手输 ID）。留空沿用默认服务商。`
      : `<b>怎么填：</b>填 AstrBot「<code>设置 → 模型服务</code>」里该服务商条目的<b>名称（Provider ID）</b>，不是接口地址、也不是模型名。例：<code>openai-gpt-4o</code>、<code>deepseek</code>。留空则沿用默认服务商。`;
    html += `
      <div class="config-item mc-role-item" data-role="${r.key}">
        <div class="mc-role-head">
          <label class="config-label">${r.label} 服务商</label>
          <button type="button" class="btn-test-provider" data-role="${r.key}" data-kind="provider" title="测试该服务商连通性">测试</button>
        </div>
        <div class="mode-toggle" style="margin-bottom:8px;">
          <button type="button" class="mode-btn mc-mode-btn ${activeMode === "pull" ? "active" : ""}" data-mode="pull" data-role="${r.key}">🔗 拉取模式</button>
          <button type="button" class="mode-btn mc-mode-btn ${activeMode === "custom" ? "active" : ""}" data-mode="custom" data-role="${r.key}">✏️ 自定义</button>
        </div>
        <div class="mc-test-status" data-role="${r.key}" data-kind="provider"></div>
        <div class="mode-panel mc-mode-panel" data-mode="pull" data-role="${r.key}" ${activeMode === "pull" ? "" : 'style="display:none"'}>
          <div class="mc-input-wrap">
            <input type="text" class="config-input mc-provider-input" list="mcProviderList" data-key="provider_${r.key}" data-role="${r.key}" autocomplete="off" value="${escapeHtml(cur)}" placeholder="选择或输入服务商 ID">
          </div>
          <p class="config-hint">${hint}</p>
        </div>
        <div class="mode-panel mc-mode-panel" data-mode="custom" data-role="${r.key}" ${activeMode === "custom" ? "" : 'style="display:none"'}>
          <label class="config-label">API Key</label>
          <input type="text" class="config-input" data-key="${r.key}_api_key" placeholder="直连服务 API Key" value="${escapeHtml(curKey)}">
          <label class="config-label">接口地址</label>
          <input type="text" class="config-input" data-key="${r.key}_api_base" placeholder="如 https://api.siliconflow.cn/v1" value="${escapeHtml(curBase)}">
          <label class="config-label">模型名</label>
          <input type="text" class="config-input" data-key="${r.key}_model" data-cats="chat" placeholder="如 Qwen/Qwen3.5-4B" value="${escapeHtml(curDModel)}">
          <p class="config-hint">自定义直连：填 Key+Base 即独立调用，<b>不依赖 AstrBot 服务商</b>；模型从顶部「模型统一配置」区拉取的列表中选择。</p>
        </div>
      </div>`;
  }

  const grid = $("mcRolesGrid");
  grid.innerHTML = html;
  grid.querySelectorAll(".mc-provider-input").forEach(inp => {
    inp.addEventListener("input", () => { MC_PROVIDER_VALS[inp.dataset.role] = inp.value.trim(); });
  });
  grid.querySelectorAll(".btn-test-provider").forEach(btn => {
    btn.addEventListener("click", () => mcTestProviderRole(btn.dataset.role));
  });
  // 能力服务商：拉取/自定义 模式切换（持久化到 localStorage，刷新不丢）
  grid.querySelectorAll(".mc-mode-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const role = btn.dataset.role;
      const mode = btn.dataset.mode;
      grid.querySelectorAll(`.mc-role-item[data-role="${role}"] .mc-mode-btn`).forEach(b => {
        b.classList.toggle("active", b === btn);
      });
      grid.querySelectorAll(`.mc-role-item[data-role="${role}"] .mc-mode-panel`).forEach(panel => {
        panel.style.display = panel.dataset.mode === mode ? "" : "none";
      });
      // 两渠道互斥（对齐「外部服务」框架）：切换即清空另一项，
      // 避免「拉取模式的服务商 + 自定义的 Key/Base」混用造成歧义。
      const item = grid.querySelector(`.mc-role-item[data-role="${role}"]`);
      if (item) {
        if (mode === "custom") {
          const pi = item.querySelector(".mc-provider-input");
          if (pi) pi.value = "";
          MC_PROVIDER_VALS[role] = "";
        } else {
          [`${role}_api_key`, `${role}_api_base`, `${role}_model`].forEach(k => {
            const inp = item.querySelector(`.config-input[data-key="${k}"]`);
            if (inp) inp.value = "";
          });
        }
      }
      try { localStorage.setItem(`mingbu.mcMode.${role}`, mode); } catch (e) { /* 忽略 */ }
    });
  });
  mcRenderModelDatalist();
  enhanceCombos(grid);
}

const MC_CAT_LABELS = { chat: "对话", vision: "视觉", embedding: "嵌入", rerank: "重排", tts: "语音合成", stt: "语音识别" };

function mcPriceText(m) {
  const p = m.price;
  if (!p) return "本地/开源 · 价格未知";
  const fmt = (v) => (v === 0 ? "免费" : (v == null ? "—" : "$" + v));
  const tag = p.source === "provider" ? "端点精确价" : "参考价";
  return `${fmt(p.in)} / ${fmt(p.out)} 每百万tok · ${tag}`;
}

function renderMcCatalog() {
  const el = $("mcCatalog");
  if (!MC_MODELS.length) {
    el.innerHTML = '<div class="empty-text">尚未拉取模型。点上方「拉取模型列表」后将在此按能力分类展示，并标出已分配的角色与价格。</div>';
    return;
  }
  const cats = ["chat", "vision", "embedding", "rerank", "tts", "stt"];
  const usedBy = {};
  for (const r of MC_DIRECT_ROLES) {
    const m = MC_ASSIGN[r.key];
    if (m) (usedBy[m] = usedBy[m] || []).push(r.label);
  }
  let html = `<div class="mc-legend">价格：端点精确价取自 /models 响应；「参考价」为内置速查表（随厂商调价变动，仅供参考）；本地/开源模型显示「价格未知」。</div>`;
  for (const cat of cats) {
    const list = MC_MODELS.filter(m => m.cats.includes(cat));
    if (!list.length) continue;
    html += `<div class="mc-cat"><div class="mc-cat-title">${MC_CAT_LABELS[cat]} <span class="mc-cat-count">${list.length}</span></div><div class="mc-chips">`;
    for (const m of list) {
      const badges = (usedBy[m.id] || []).map(b => `<span class="mc-badge">${escapeHtml(b)}</span>`).join("");
      html += `<div class="mc-model-chip ${badges ? "mc-chip-used" : ""}">
        <span class="mc-model-id">${escapeHtml(m.id)}</span>
        <span class="mc-model-price">${escapeHtml(mcPriceText(m))}</span>
        ${badges ? `<span class="mc-badges">${badges}</span>` : ""}
      </div>`;
    }
    html += `</div></div>`;
  }
  el.innerHTML = html || '<div class="empty-text">无匹配模型</div>';
}

// ===== 外部服务卡片：拉取模式 / 自定义模式 切换 =====
function svcSetMode(card, mode, clearInactive) {
  card.querySelectorAll(".mode-btn").forEach(b => b.classList.toggle("active", b.dataset.mode === mode));
  card.querySelectorAll(".mode-panel").forEach(p => { p.style.display = p.dataset.mode === mode ? "" : "none"; });
  // 自动补全依赖此状态类判断当前是否为自定义模式；此前只切换了面板，
  // 没有同步 mode-custom，导致 svcAutofillCard 始终提前 return。
  card.classList.toggle("mode-custom", mode === "custom");
  card.dataset.mode = mode;
  if (!clearInactive) return;
  const pk = card.dataset.providerKey;
  const custom = (card.dataset.customKeys || "").split(",").filter(Boolean);
  if (mode === "custom") {
    const inp = document.querySelector(`.config-input[data-key="${pk}"]`);
    if (inp) inp.value = "";
  } else {
    custom.forEach(k => { const inp = document.querySelector(`.config-input[data-key="${k}"]`); if (inp) inp.value = ""; });
  }
}
function initExternalServiceToggles() {
  document.querySelectorAll(".svc-card").forEach(card => {
    const custom = (card.dataset.customKeys || "").split(",").filter(Boolean);
    const hasCustom = custom.some(k => {
      const inp = document.querySelector(`.config-input[data-key="${k}"]`);
      return inp && inp.value.trim() !== "";
    });
    svcSetMode(card, hasCustom ? "custom" : "pull", false);
  });
}

init();

// ===== 音色库模块 =====
let TTS_VOICES = [];

async function loadTtsVoices(opts) {
  // v4.9.3：opts 仅剩 { silent }——「拉取音色」按钮已删除，本函数只在
  // initTtsTab 时静默调用一次（后端 skip_verify 模式，零成本返回静态表）。
  const { silent = false } = opts || {};
  // 检查 bridge 是否可用（脱离 AstrBot 面板时 window.AstrBotPluginPage 未注入）
  if (!bridge) {
    if (!silent) {
      alert("⚠️ 管理面板未就绪：当前页面需通过 AstrBot 管理面板（/admin/mingbu）访问，\n直接打开 HTML 文件无法调用后端 API。\n\n请在 AstrBot 后台 → 插件 → 命灵 AI 伴侣 → 管理控制台 中打开此页面。");
    }
    return;
  }
  try {
    // 兜底：CONFIG_DATA 仅在切到「核心配置」页时加载；直接进本页时先拉配置
    if (Object.keys(CONFIG_DATA).length === 0) {
      try {
        CONFIG_DATA = (await apiGet("config", { v: Date.now() })) || {};
      } catch (_) { /* 拉取失败不阻塞音色列表 */ }
    }
    const res = await apiPost("tts/voices", { provider: "dashscope", skip_verify: true });
    if (!res.ok) {
      if (!silent) alert(res.error || "拉取音色列表失败");
      return;
    }
    TTS_VOICES = res.voices || [];

    // 填充数据层 select（「留空=默认」占位 option 由 HTML 静态提供）
    const voiceSelect = document.querySelector('.config-input[data-key="tts_voice"]');
    if (voiceSelect && voiceSelect.tagName.toLowerCase() === "select") {
      [...voiceSelect.options].slice(1).forEach(o => o.remove());
      TTS_VOICES.forEach(v => {
        const opt = document.createElement("option");
        opt.value = v.voice;
        opt.textContent = `${v.voice} - ${v.description.split("，")[0] || v.description}`;
        voiceSelect.appendChild(opt);
      });
      // 恢复当前已保存音色的选中态（值不在列表中则保持原值/留空）
      const savedVoice = voiceSelect.value || (CONFIG_DATA && CONFIG_DATA.tts_voice) || "";
      if (savedVoice && TTS_VOICES.some(v => v.voice === savedVoice)) {
        voiceSelect.value = savedVoice;
      }
    }
    renderVoiceDropdown();
  } catch (e) {
    if (!silent) alert("拉取音色列表失败：" + e.message);
  }
}

// ── v4.9.3：自定义音色下拉（表现层）─────────────────────────────────────
// 原生 <select> 的 option 无法内嵌按钮；下拉面板由 JS 渲染，每行 =
// 音色描述 + 🔊 试听按钮。数据层永远是隐藏的 tts_voice select。

function voiceLabelOf(voice) {
  const v = TTS_VOICES.find(x => x.voice === voice);
  return v ? `${v.voice} - ${v.description.split("，")[0] || v.description}` : voice;
}

function syncVoiceTrigger() {
  const voiceSelect = document.querySelector('.config-input[data-key="tts_voice"]');
  const label = $("voiceDdLabel");
  if (!voiceSelect || !label) return;
  const val = voiceSelect.value;
  label.textContent = val ? voiceLabelOf(val) : "留空＝接口默认（SiliconFlow 为 alex）";
  label.classList.toggle("is-empty", !val);
}

function renderVoiceDropdown() {
  const panel = $("voiceDdPanel");
  if (!panel) return;
  const voiceSelect = document.querySelector('.config-input[data-key="tts_voice"]');
  const current = voiceSelect ? voiceSelect.value : "";
  let html = `<button type="button" class="voice-dd-item ${current ? "" : "selected"}" data-pick="">` +
    `<span class="voice-dd-name">留空＝接口默认（SiliconFlow 为 alex）</span></button>`;
  TTS_VOICES.forEach(v => {
    const selected = v.voice === current;
    html += `
      <div class="voice-dd-item ${selected ? "selected" : ""}">
        <button type="button" class="voice-dd-pick" data-pick="${escapeHtml(v.voice)}" title="选用 ${escapeHtml(v.voice)}">
          <span class="voice-dd-name">${escapeHtml(v.voice)}</span>
          <span class="voice-dd-desc">${escapeHtml(v.description)}</span>
        </button>
        <button type="button" class="voice-dd-preview" data-preview="${escapeHtml(v.voice)}" title="试听 ${escapeHtml(v.voice)}">🔊</button>
      </div>`;
  });
  panel.innerHTML = html;
  syncVoiceTrigger();
}

function toggleVoiceDropdown(force) {
  const panel = $("voiceDdPanel");
  if (!panel) return;
  const show = typeof force === "boolean" ? force : panel.hidden;
  panel.hidden = !show;
  const trig = $("voiceDdTrigger");
  trig?.classList.toggle("open", show);
  trig?.setAttribute("aria-expanded", show ? "true" : "false");
}

function pickVoice(voiceName) {
  // 选中音色：写数据层 select → 同步触发器 → 保存单键 → 收起面板
  const voiceSelect = document.querySelector('.config-input[data-key="tts_voice"]');
  if (voiceSelect) {
    voiceSelect.value = voiceName || "";
    voiceSelect.dispatchEvent(new Event("change", { bubbles: true }));
  }
  renderVoiceDropdown();
  toggleVoiceDropdown(false);
}

async function previewVoice(voiceName, type = "短文本") {
  // v4.9.3：试听入口收敛为下拉面板里的 🔊。试听文本用固定短句（原
  // previewText 输入框已随音色库区块移除）；provider 固定 dashscope
  // （内置 24 音色均为 DashScope 官方音色）；语气指令不再暴露。
  if (!bridge) {
    alert("⚠️ 管理面板未就绪：当前页面需通过 AstrBot 管理面板（/admin/mingbu）访问，\n直接打开 HTML 文件无法调用后端 API。\n\n请在 AstrBot 后台 → 插件 → 命灵 AI 伴侣 → 管理控制台 中打开此页面。");
    return;
  }
  const text = "你好，今天天气真好";
  const btn = document.querySelector(`.voice-dd-preview[data-preview="${CSS.escape(voiceName)}"]`);
  if (btn) { btn.classList.add("playing"); btn.textContent = "⏳"; }
  toast(`试听中：${voiceName}…`);
  try {
    const res = await apiPost("tts/preview", { text, voice: voiceName, provider: "dashscope" });
    if (!res.ok) {
      alert(res.error || "试听失败");
      return;
    }
    // 播放音频（动态元素，原 previewAudio 已随音色库区块移除）
    const audio = new Audio(URL.createObjectURL(base64ToBlob(res.audio_b64, "audio/wav")));
    audio.onended = () => URL.revokeObjectURL(audio.src);
    await audio.play();
    toast(`🔊 ${voiceName} · ${(res.duration_ms / 1000).toFixed(1)} 秒`);
  } catch (e) {
    alert("试听失败：" + e.message);
  } finally {
    if (btn) { btn.classList.remove("playing"); btn.textContent = "🔊"; }
  }
}

function base64ToBlob(base64, mimeType) {
  const byteChars = atob(base64);
  const byteArrays = [];
  for (let offset = 0; offset < byteChars.length; offset += 512) {
    const slice = byteChars.slice(offset, offset + 512);
    const byteNumbers = new Array(slice.length);
    for (let i = 0; i < slice.length; i++) {
      byteNumbers[i] = byteChars.charCodeAt(offset + i);
    }
    byteArrays.push(new Uint8Array(byteNumbers));
  }
  return new Blob(byteArrays, { type: mimeType });
}

function initTtsTab() {
  const voiceSelect = document.querySelector('.config-input[data-key="tts_voice"]');
  const trigger = $("voiceDdTrigger");
  const panel = $("voiceDdPanel");

  // 展开/收起（div 触发器：click + 键盘 Enter/Space 可达）
  trigger?.addEventListener("click", (e) => {
    e.stopPropagation();
    toggleVoiceDropdown();
  });
  trigger?.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      e.stopPropagation();
      toggleVoiceDropdown();
    }
  });

  // 面板事件委托：点行（data-pick）= 选用并保存；点 🔊（data-preview）= 仅试听
  panel?.addEventListener("click", (e) => {
    const previewBtn = e.target.closest("button[data-preview]");
    if (previewBtn) {
      e.stopPropagation();
      previewVoice(previewBtn.dataset.preview);
      return;
    }
    const pickBtn = e.target.closest("[data-pick]");
    if (pickBtn) {
      e.stopPropagation();
      pickVoice(pickBtn.dataset.pick);
    }
  });

  // 点面板外 / Esc 收起
  document.addEventListener("click", (e) => {
    if (!panel?.hidden && !e.target.closest("#voiceDropdown")) {
      toggleVoiceDropdown(false);
    }
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") toggleVoiceDropdown(false);
  });

  // 数据层 select 变化（pickVoice 触发）：同步触发器/面板选中态 + 立即保存
  voiceSelect?.addEventListener("change", () => {
    syncVoiceTrigger();
    renderVoiceDropdown();
    saveTtsVoiceValue(voiceSelect.value);
  });

  // 页面加载即零成本填充下拉（skip_verify 不调外部 API、无 Key 也可用）
  loadTtsVoices({ silent: true });
}

// 选用音色并立即保存：只提交 tts_voice 单键，后端与既有运行时配置合并，
// 不会清掉其它已存键（coerce_by_schema 无 required 全量校验）。空值 = 清空
// 音色（回到接口默认），同样允许保存。
async function saveTtsVoiceValue(voiceName) {
  if (!bridge) {
    toast("面板未就绪，无法保存音色", true);
    return;
  }
  try {
    const res = await apiPost("config", { tts_voice: voiceName });
    if (res && res.ok === false) {
      toast("保存失败：" + (res.error || "未知错误"), true);
      return;
    }
    if (CONFIG_DATA) CONFIG_DATA.tts_voice = voiceName;
    toast(voiceName ? `已保存音色：${voiceName} ✨ 发消息即生效` : "已清空音色（回接口默认）");
  } catch (e) {
    toast("保存失败：" + e.message + "（也可到「核心配置」页点保存全部）", true);
  }
}

// v4.9.0：initTtsTabIfNeeded 已删除（原「音色库」独立 Tab 不复存在）。
// v4.9.3：音色库卡片/试听区移除，试听入口收敛进「音色」下拉面板（🔊）。
