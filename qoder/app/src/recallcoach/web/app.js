const $ = (selector) => document.querySelector(selector);

const state = {
  sessionId: null,
  current: null,
  personalization: true,
  report: "",
};

const REASONS = {
  correct: "答对了，只更新调度",
  "lapses<2": "同一知识点首次失败，先观察",
  "daily-cap": "今日写入已达上限",
  "personalization-off": "对照模式（off）：本轮不读写记忆",
  dedupe: "已合并到已有条目",
  forbidden: "内容不合规，未写入",
  "threshold-met": "达到阈值，写入薄弱点",
  "concept-mix": "错因里确认了混淆对象",
  "session-end": "会话结束，记录进度",
  "same-day": "今日已有进度条目，改为修订",
  "empty-session": "本轮没有作答",
};

const KINDS = { weakness: "薄弱点", misconception: "混淆", progress: "进度" };

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (ch) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[ch]);
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      detail = body.detail || JSON.stringify(body);
    } catch (_) { /* 保留状态文本 */ }
    throw new Error(detail);
  }
  return response.json();
}

let toastTimer = null;
function toast(message, kind = "ok") {
  const el = $("#toast");
  el.textContent = message;
  el.style.borderLeftColor = kind === "warn" ? "var(--warn)" : kind === "bad" ? "var(--danger)" : "var(--ok)";
  el.classList.remove("hidden");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.add("hidden"), 4200);
}

async function loadHealth() {
  try {
    const health = await api("/api/health");
    const pills = [
      ["ok", "PowerContext 就绪"],
      ["", `日期 ${health.date}`],
      ["", `卡片 ${health.cards}`],
      ["", `记忆指针 ${health.memory_refs}`],
    ];
    if (!health.pc_ready) {
      pills[0] = ["bad", "PowerContext 离线（只按到期排序）"];
    } else if (health.memory_search_modes) {
      pills.push(["", `检索 ${[].concat(health.memory_search_modes).join("/")}`]);
    }
    $("#status").innerHTML = pills
      .map(([cls, text]) => `<span class="pill ${cls}">${esc(text)}</span>`)
      .join("");
  } catch (error) {
    $("#status").innerHTML = `<span class="pill bad">状态读取失败：${esc(error.message)}</span>`;
  }
}

function breakdownChips(item) {
  const b = item.breakdown || {};
  const parts = [
    ["到期", b.due],
    ["薄弱", b.weakness],
    ["混淆", b.misconception],
    ["进度", b.progress],
    ["疲劳", b.fatigue],
  ].filter(([, value]) => value);
  return parts
    .map(([label, value]) => `<span class="chip">${label} ${value > 0 ? "+" : ""}${value}</span>`)
    .join("");
}

function coachNoteHtml(raw) {
  // prepared context 带大量审计元数据：页面只展示可直接读的条目，原文折叠备查。
  const bullets = (raw.match(/\[kp:[^\]\n]+\][^\n]*/g) || [])
    .map((line) => line.replace(/\s+$/, "").replace(/^>\s*/, ""))
    .slice(0, 4);
  const body = bullets.length
    ? `<ul class="coach-list">${bullets.map((item) => `<li>${esc(item)}</li>`).join("")}</ul>`
    : `<p class="muted">这段上下文里没有可直接展示的条目。</p>`;
  return `<div class="coach-head">教练备忘 <span class="muted">来自 PowerContext 的历史记忆</span></div>
    ${body}
    <details><summary>查看原始上下文</summary><pre class="coach-raw">${esc(raw)}</pre></details>`;
}

async function loadToday() {
  const mode = state.personalization ? "on" : "off";
  try {
    const data = await api(`/api/today?personalization=${mode}`);
    state.sessionId = data.session_id;

    const note = $("#coach-note");
    if (data.mode === "offline") {
      note.textContent = "记忆离线：当前只按到期排序，不会读写记忆。启动 PowerContext 后刷新即可。";
      note.classList.remove("hidden");
    } else if (data.coach_note) {
      note.innerHTML = coachNoteHtml(data.coach_note);
      note.classList.remove("hidden");
    } else if (data.mode === "on") {
      note.textContent = "暂无历史记忆：这是本轮第一次，按默认讲法开始。";
      note.classList.remove("hidden");
    } else {
      note.classList.add("hidden");
    }

    $("#queue-meta").textContent = `共 ${data.items.length} 张 · 模式 ${data.mode} · 会话 ${data.session_id}`
      + (data.mode === "off" ? " · 对照模式：本轮不读写记忆" : "");

    const queue = $("#queue");
    if (!data.items.length) {
      queue.innerHTML = `<p class="muted">今天没有到期卡片。去「我记得你」看看已记录的内容。</p>`;
      return;
    }
    queue.innerHTML = data.items.map((item, index) => `
      <article class="card" data-id="${esc(item.id)}">
        <div class="card-head">
          <span class="rank">#${index + 1} · ${esc(item.chapter_title || "")}</span>
          <span class="chip score">score ${item.score}</span>
        </div>
        <div class="card-front">${esc(item.front)}</div>
        <div class="chips">
          <span class="chip">${esc(item.kp_name || item.kp_id)}</span>
          ${breakdownChips(item)}
          ${(item.memory_hits || []).map((hit) => `<span class="chip hit">${esc(hit.kind)} ${esc(hit.entry_id)}</span>`).join("")}
        </div>
      </article>
    `).join("");

    queue.querySelectorAll(".card").forEach((node) => {
      node.addEventListener("click", () => {
        const item = data.items.find((entry) => entry.id === node.dataset.id);
        if (item) openCard(item);
      });
    });
  } catch (error) {
    toast(`加载失败：${error.message}`, "bad");
  }
}

function openCard(item) {
  state.current = item;
  $("#panel-chapter").textContent = `${item.chapter_title || ""} · ${item.kp_name || ""} · score ${item.score}`;
  $("#panel-front").textContent = item.front;
  $("#panel-back").textContent = item.back;
  $("#panel-explanation").textContent = item.explanation;
  $("#panel-answer").classList.add("hidden");
  $("#panel-reveal").classList.remove("hidden");
  $("#panel-cause").value = "";
  $("#panel-mix").value = "";
  $("#panel-mix-wrap").classList.add("hidden");
  $("#answer-panel").classList.remove("hidden");
}

function closePanel() {
  $("#answer-panel").classList.add("hidden");
  state.current = null;
}

function memoryToast(result) {
  // 一次作答可能同时产出薄弱点与混淆两条决策，逐条显示
  const decisions = [].concat(result.decision || []);
  const memories = [].concat(result.memory || []);
  const lines = [];
  let failed = false;
  let warned = false;
  memories.forEach((memory, index) => {
    const decision = memory.decision || decisions[index] || {};
    const kind = KINDS[decision.kind] || decision.kind || "记忆";
    if (memory.status === "written") {
      lines.push(`已记下${kind}：${memory.entry_id}（v${memory.version ?? 1}）`);
    } else if (memory.status === "revised") {
      lines.push(`已修订${kind}：${memory.entry_id} → v${memory.version ?? "?"}`);
    } else if (memory.status === "pending") {
      warned = true;
      lines.push(`今日写入已达上限，这条${kind}进"待确认"（内容已记审计，没有静默丢弃）`);
    } else if (memory.status === "error") {
      failed = true;
      lines.push(`${kind}写入失败：${memory.error}`);
    }
  });
  if (!lines.length) {
    const decision = decisions[0] || {};
    lines.push(`本次不写记忆：${REASONS[decision.reason] || decision.reason || "跳过"}`);
  }
  toast(lines.join("　·　"), failed ? "bad" : warned ? "warn" : "ok");
}

async function submitAnswer(correct, confidence) {
  if (!state.current) return;
  const cardId = state.current.id;
  const picked = $("#panel-cause").value;
  const mix = $("#panel-mix").value.trim();
  // 只有"概念混淆 + 用户写出对象"才拼成带对象的错因；否则保持原选项
  const cause = picked === "概念混淆" && mix ? `概念混淆：${mix}` : picked || null;
  try {
    const result = await api("/api/answer", {
      method: "POST",
      body: JSON.stringify({
        card_id: cardId,
        correct,
        confidence,
        cause,
        latency_ms: 0,
        session_id: state.sessionId,
      }),
    });
    closePanel();
    memoryToast(result);
    await loadToday();
    await loadHealth();
  } catch (error) {
    toast(`提交失败：${error.message}`, "bad");
  }
}

async function endSession() {
  if (!state.sessionId) return;
  try {
    const result = await api("/api/session/end", {
      method: "POST",
      body: JSON.stringify({ session_id: state.sessionId }),
    });
    const note = $("#session-summary");
    note.textContent = `本轮结束：${result.total} 张，答对 ${result.correct}。`
      + (result.stuck.length ? `\n卡住：${result.stuck.join("、")}` : "")
      + `\n下次从 ${result.next_start || "今天队列首位"} 开始。`
      + (result.progress_entry_id ? `\n进度已记下：${result.progress_entry_id}` : "\n本轮未写记忆。");
    note.classList.remove("hidden");
    toast(result.progress_entry_id ? "已记录本轮进度" : "本轮结束");
    await loadToday();
  } catch (error) {
    toast(`结束失败：${error.message}`, "bad");
  }
}

function movedText(moved) {
  const rows = [].concat(moved || []);
  if (!rows.length) return "队列顺序没有变化。";
  return `队列已重排：${rows.map((row) => `${row.card_id} #${row.before}→${row.after ?? "未进前十"}`).join("、")}`;
}

async function loadReplay() {
  const container = $("#replay-list");
  container.innerHTML = `<p class="muted">读取中…</p>`;
  $("#replay-order").textContent = "";
  try {
    const data = await api("/api/replay");
    if (data.note) {
      container.innerHTML = `<p class="muted">${esc(data.note)}</p>`;
      return;
    }
    if (!data.entries.length) {
      container.innerHTML = `<p class="muted">还没有记忆条目：答错同一个知识点两次之后就会出现。</p>`;
      return;
    }
    container.innerHTML = data.entries.map((entry) => `
      <article class="entry" data-id="${esc(entry.entry_id)}">
        <div class="entry-head">
          <span class="chip hit">${esc(entry.kind || "unknown")}</span>
          <span class="chip">${esc(entry.entry_id)}</span>
          <span class="chip">v${esc(entry.version ?? "?")}</span>
          <span class="chip">${esc(entry.state || "")}</span>
          ${(entry.hit_cards || []).map((id) => `<span class="chip">命中 ${esc(id)}</span>`).join("")}
        </div>
        <textarea spellcheck="false">${esc(entry.text || "")}</textarea>
        <div class="entry-actions">
          <button class="ghost save">保存改写</button>
          <button class="ghost retire">已掌握</button>
        </div>
      </article>
    `).join("");

    container.querySelectorAll(".entry").forEach((node) => {
      const entryId = node.dataset.id;
      node.querySelector(".save").addEventListener("click", async () => {
        const text = node.querySelector("textarea").value.trim();
        try {
          const result = await api(`/api/memory/${encodeURIComponent(entryId)}/revise`, {
            method: "POST",
            body: JSON.stringify({ text }),
          });
          if (!result.ok) {
            toast(`保存失败：${result.error}`, "bad");
            return;
          }
          toast(`已保存为 v${result.version}`);
          await loadReplay();
          $("#replay-order").textContent = movedText(result.moved);
          await loadToday();
        } catch (error) {
          toast(`保存失败：${error.message}`, "bad");
        }
      });
      node.querySelector(".retire").addEventListener("click", async () => {
        try {
          const result = await api(`/api/memory/${encodeURIComponent(entryId)}/retire`, {
            method: "POST",
            body: JSON.stringify({ reason: "用户标记已掌握" }),
          });
          if (!result.ok) {
            toast(`标记失败：${result.error}`, "bad");
            return;
          }
          toast("已标记为掌握，队列会立刻重排");
          await loadReplay();
          $("#replay-order").textContent = movedText(result.moved);
          await loadToday();
        } catch (error) {
          toast(`标记失败：${error.message}`, "bad");
        }
      });
    });
  } catch (error) {
    container.innerHTML = `<p class="muted">读取失败：${esc(error.message)}</p>`;
  }
}

async function loadCompare() {
  const container = $("#compare");
  try {
    const data = await api("/api/compare?limit=5");
    const list = (items, withHits) => `<ol>${items.map((item) => `
      <li>${esc(item.id)} · ${esc(item.kp_name)} · <span class="chip score">${item.score}</span>
        ${withHits && (item.memory_hits || []).length ? `<span class="chip hit">${esc(item.memory_hits[0].entry_id)}</span>` : ""}
      </li>`).join("")}</ol>`;
    const diff = data.diff.length
      ? `<table><thead><tr><th>卡片</th><th>个性化</th><th>基线</th></tr></thead><tbody>
          ${data.diff.map((row) => `<tr><td>${esc(row.card_id)}</td><td>#${row.on}</td><td>${row.off ? `#${row.off}` : "未进前五"}</td></tr>`).join("")}
        </tbody></table>`
      : `<p class="muted">没有位次差异：当前记忆还没有命中今日队列里的卡片。</p>`;
    container.innerHTML = `
      <div><h3>个性化（读记忆）</h3>${list(data.on, true)}</div>
      <div><h3>基线（不读记忆）</h3>${list(data.off, false)}</div>
      <div class="diff-row"><h3>位次差异</h3>${diff}</div>
    `;
  } catch (error) {
    container.innerHTML = `<p class="muted">读取失败：${esc(error.message)}</p>`;
  }
}

async function loadEvidence() {
  const container = $("#evidence-table");
  try {
    const data = await api("/api/evidence?limit=50");
    state.report = data.report_markdown || "";
    if (!data.audit.length) {
      container.innerHTML = `<p class="muted">还没有调用记录：先去答几张卡。</p>`;
      return;
    }
    container.innerHTML = `
      <table>
        <thead><tr><th>时间</th><th>操作</th><th>request_id</th><th>耗时</th><th>状态</th></tr></thead>
        <tbody>
          ${data.audit.slice().reverse().map((row) => `
            <tr>
              <td>${esc((row.ts || "").replace("T", " ").slice(5, 19))}</td>
              <td class="op">${esc(row.op)}</td>
              <td class="req">${esc(row.request_id || "—")}</td>
              <td>${esc(row.latency_ms)}ms</td>
              <td>${esc(row.status)}</td>
            </tr>`).join("")}
        </tbody>
      </table>`;
  } catch (error) {
    container.innerHTML = `<p class="muted">读取失败：${esc(error.message)}</p>`;
  }
}

function exportReport() {
  if (!state.report) {
    toast("报告还没生成，先刷新证据页", "warn");
    return;
  }
  const blob = new Blob([state.report], { type: "text/markdown;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `recallcoach-report-${new Date().toISOString().slice(0, 10)}.md`;
  link.click();
  URL.revokeObjectURL(url);
}

function switchTab(name) {
  document.querySelectorAll("#tabs button[data-tab]").forEach((button) => {
    button.classList.toggle("active", button.dataset.tab === name);
  });
  document.querySelectorAll(".tab").forEach((section) => {
    section.classList.toggle("active", section.id === `tab-${name}`);
  });
  if (name === "replay") loadReplay();
  if (name === "compare") loadCompare();
  if (name === "evidence") loadEvidence();
  if (name === "today") loadToday();
}

function wire() {
  document.querySelectorAll("#tabs button[data-tab]").forEach((button) => {
    button.addEventListener("click", () => switchTab(button.dataset.tab));
  });
  $("#refresh").addEventListener("click", () => {
    const active = document.querySelector("#tabs button.active").dataset.tab;
    loadHealth();
    switchTab(active);
  });
  $("#personalization").addEventListener("change", (event) => {
    state.personalization = event.target.checked;
    loadToday();
  });
  $("#end-session").addEventListener("click", endSession);
  $("#panel-close").addEventListener("click", closePanel);
  $("#panel-reveal").addEventListener("click", () => {
    $("#panel-answer").classList.remove("hidden");
    $("#panel-reveal").classList.add("hidden");
  });
  // 选了"概念混淆"才追问混淆对象：没有对象就不写这条判断（memory-design.md 第 3 节）
  $("#panel-cause").addEventListener("change", (event) => {
    const wrap = $("#panel-mix-wrap");
    wrap.classList.toggle("hidden", event.target.value !== "概念混淆");
    if (event.target.value !== "概念混淆") $("#panel-mix").value = "";
  });
  document.querySelectorAll(".rating button").forEach((button) => {
    button.addEventListener("click", () => {
      submitAnswer(button.dataset.correct === "1", Number(button.dataset.confidence));
    });
  });
  $("#export-report").addEventListener("click", exportReport);
}

wire();
loadHealth();

// 支持 #today / #replay / #compare / #evidence 直接打开某个页签（截图与分享都用得上）
const initialTab = (window.location.hash || "").replace("#", "");
if (["today", "replay", "compare", "evidence"].includes(initialTab)) {
  switchTab(initialTab);
} else {
  loadToday();
}
