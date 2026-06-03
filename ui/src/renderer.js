import React from 'react';
  
  const Renderer = () =>  {
	return (
	  <div>
	  </div>
	);
  }
  
  export default Renderer;
  /**
 * JARVIS Renderer — ui/src/renderer.js
 * All UI logic: chat, voice control, system monitor, settings.
 * Runs in the browser context, communicates via window.jarvis (preload bridge).
 */

// ─── State ─────────────────────────────────────────────────
const state = {
  view: "chat",
  voiceActive: false,
  isThinking: false,
  backendOnline: false,
  settings: {},
  sysRefreshTimer: null,
};

// ─── Init ──────────────────────────────────────────────────

document.addEventListener("DOMContentLoaded", async () => {
  await loadTheme();
  setupInputHandlers();
  setupIpcListeners();
  await fetchStatus();
  setInterval(fetchStatus, 10000);  // Refresh status every 10s
});

// ─── View switching ────────────────────────────────────────

function switchView(view) {
  state.view = view;

  document.querySelectorAll(".nav-item").forEach(el => {
    el.classList.toggle("active", el.dataset.view === view);
  });

  document.getElementById("chat-view").classList.toggle("hidden", view !== "chat");
  document.getElementById("system-view").classList.toggle("hidden", view !== "system");
  document.getElementById("settings-view").classList.toggle("hidden", view !== "settings");

  if (view === "system") startSysMonitor();
  else stopSysMonitor();

  if (view === "settings") loadSettings();
}

// ─── Chat ──────────────────────────────────────────────────

function setupInputHandlers() {
  const input = document.getElementById("user-input");

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });
}

async function sendMessage() {
  const input = document.getElementById("user-input");
  const text = input.value.trim();
  if (!text || state.isThinking) return;

  input.value = "";
  input.style.height = "auto";

  appendMessage("user", text);
  await askJarvis(text);
}

async function askJarvis(text) {
  state.isThinking = true;
  setStatusDot("thinking");
  document.getElementById("status-text").textContent = "Thinking...";

  const typingId = showTyping();
  const t0 = Date.now();

  try {
    const res = await jarvis.chat(text);
    removeTyping(typingId);

    if (res && res.text) {
      appendMessage("assistant", res.text, {
        provider: res.provider,
        model: res.model,
        latency: Date.now() - t0,
        tokens: res.tokens_used,
      });
      document.getElementById("active-provider").textContent =
        `${res.provider} / ${res.model || ""}`;
      document.getElementById("last-latency").textContent =
        `${Date.now() - t0}ms`;
    } else {
      appendMessage("assistant", res?.error || "No response received.");
    }
  } catch (err) {
    removeTyping(typingId);
    appendMessage("assistant", `Connection error: ${err.message}`);
  }

  state.isThinking = false;
  setStatusDot(state.backendOnline ? "online" : "offline");
  document.getElementById("status-text").textContent =
    state.backendOnline ? "Online" : "Offline";
}

function appendMessage(role, text, meta = {}) {
  const messages = document.getElementById("messages");
  const time = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });

  const wrapper = document.createElement("div");
  wrapper.className = `message ${role}`;

  const avatar = document.createElement("div");
  avatar.className = "msg-avatar";
  avatar.innerHTML = role === "assistant"
    ? '<i class="ti ti-robot"></i>'
    : '<i class="ti ti-user"></i>';

  const body = document.createElement("div");

  const bubble = document.createElement("div");
  bubble.className = "msg-bubble";
  bubble.innerHTML = renderMarkdown(text);
  bubble.querySelectorAll("code").forEach(el => el.setAttribute("user-select", "text"));

  const metaEl = document.createElement("div");
  metaEl.className = "msg-meta";
  if (meta.provider) {
    metaEl.textContent = `${time} · ${meta.provider}${meta.latency ? ` · ${meta.latency}ms` : ""}`;
  } else {
    metaEl.textContent = time;
  }

  body.appendChild(bubble);
  body.appendChild(metaEl);
  wrapper.appendChild(avatar);
  wrapper.appendChild(body);
  messages.appendChild(wrapper);

  messages.scrollTop = messages.scrollHeight;
}

function showTyping() {
  const messages = document.getElementById("messages");
  const id = "typing-" + Date.now();

  const wrapper = document.createElement("div");
  wrapper.className = "message assistant";
  wrapper.id = id;
  wrapper.innerHTML = `
    <div class="msg-avatar"><i class="ti ti-robot"></i></div>
    <div>
      <div class="msg-bubble">
        <div class="typing-indicator">
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
          <div class="typing-dot"></div>
        </div>
      </div>
    </div>`;

  messages.appendChild(wrapper);
  messages.scrollTop = messages.scrollHeight;
  return id;
}

function removeTyping(id) {
  document.getElementById(id)?.remove();
}

// ─── Voice ─────────────────────────────────────────────────

async function toggleVoice() {
  if (state.voiceActive) cancelVoice();
  else await startVoice();
}

async function startVoice() {
  state.voiceActive = true;
  const overlay = document.getElementById("voice-overlay");
  const btn = document.getElementById("voice-btn");
  const label = document.getElementById("voice-label");

  overlay.classList.add("visible");
  btn.classList.add("recording");
  btn.querySelector("i").className = "ti ti-microphone-off";
  label.textContent = "Listening...";

  try {
    const res = await jarvis.voiceStart();
    if (res?.text) {
      cancelVoice();
      document.getElementById("user-input").value = res.text;
      sendMessage();
    } else {
      label.textContent = res?.error || "Could not hear you.";
      setTimeout(cancelVoice, 1500);
    }
  } catch (e) {
    cancelVoice();
  }
}

function cancelVoice() {
  state.voiceActive = false;
  document.getElementById("voice-overlay").classList.remove("visible");
  const btn = document.getElementById("voice-btn");
  btn.classList.remove("recording");
  btn.querySelector("i").className = "ti ti-microphone";
  jarvis.voiceStop().catch(() => {});
}

function attachFile() {
  // File attach — sends file path to Python backend
  const input = document.createElement("input");
  input.type = "file";
  input.onchange = () => {
    if (input.files[0]) {
      document.getElementById("user-input").value =
        `[File: ${input.files[0].name}] ` + document.getElementById("user-input").value;
    }
  };
  input.click();
}

// ─── Status ────────────────────────────────────────────────

async function fetchStatus() {
  try {
    const status = await jarvis.getStatus();
    if (!status || status.error) {
      setOnline(false);
      return;
    }

    setOnline(true);

    // Provider list
    const providerList = document.getElementById("provider-list");
    const brain = status.brain || {};
    providerList.innerHTML = Object.entries(brain).map(([name, alive]) => `
      <div class="provider-badge">
        <div class="dot" style="background:${alive ? "var(--success)" : "var(--text-dim)"}"></div>
        ${name}
      </div>`).join("");

    // Memory stat
    const mem = status.memory || {};
    document.getElementById("memory-stat").innerHTML = `
      <i class="ti ti-database" style="font-size:12px"></i>
      <span>Memory: ${mem.total_memories ?? "—"} entries</span>`;

  } catch {
    setOnline(false);
  }
}

function setOnline(online) {
  state.backendOnline = online;
  setStatusDot(online ? "online" : "offline");
  document.getElementById("status-text").textContent = online ? "Online" : "Offline";
}

function setStatusDot(state) {
  const dot = document.getElementById("status-dot");
  dot.className = `status-dot ${state}`;
}

// ─── System monitor ────────────────────────────────────────

function startSysMonitor() {
  refreshSys();
  state.sysRefreshTimer = setInterval(refreshSys, 3000);
}

function stopSysMonitor() {
  clearInterval(state.sysRefreshTimer);
}

async function refreshSys() {
  try {
    const info = await jarvis.getSystemInfo();
    if (!info || info.error) return;

    setBar("cpu", info.cpu_percent, `${info.cpu_percent}`, "percent");
    setBar("ram", info.ram_percent, `${info.ram_used_gb}`, `/ ${info.ram_total_gb} GB`);
    setBar("disk", info.disk_percent, `${info.disk_used_gb}`, `/ ${info.disk_total_gb} GB`);

    if (info.battery) {
      setBar("battery", info.battery.percent, `${Math.round(info.battery.percent)}`,
        info.battery.plugged ? "% · charging" : "%");
    } else {
      document.getElementById("battery-card").style.display = "none";
    }

    document.getElementById("sys-updated").textContent =
      "Updated: " + new Date().toLocaleTimeString();
  } catch {}
}

function setBar(id, pct, valText, unitText) {
  const val = document.getElementById(`${id}-val`);
  const unit = document.getElementById(`${id}-unit`);
  const bar = document.getElementById(`${id}-bar`);
  if (val) val.textContent = valText;
  if (unit) unit.textContent = unitText;
  if (bar) {
    bar.style.width = `${Math.min(100, pct)}%`;
    bar.className = `progress-fill${pct > 85 ? " danger" : pct > 70 ? " warn" : ""}`;
  }
}

// ─── Settings ──────────────────────────────────────────────

async function loadSettings() {
  try {
    const s = await jarvis.getSettings();
    state.settings = s || {};

    setToggle("wake-toggle", s.wake_word_enabled !== false);
    setToggle("stream-tts-toggle", s.streaming_tts !== false);
    setToggle("search-toggle", s.web_search_enabled !== false);
    setToggle("mem-toggle", s.memory_enabled !== false);

    if (s.tts_provider) document.getElementById("tts-select").value = s.tts_provider;
    if (s.whisper_model) document.getElementById("stt-select").value = s.whisper_model;
    if (s.ai_provider) document.getElementById("ai-select").value = s.ai_provider;
  } catch {}
}

function setToggle(id, on) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle("on", on);
}

function toggleSetting(el, key) {
  el.classList.toggle("on");
  const val = el.classList.contains("on");
  state.settings[key] = val;
  jarvis.saveSettings(state.settings).catch(() => {});
}

function saveSetting(key, value) {
  state.settings[key] = value;
  jarvis.saveSettings(state.settings).catch(() => {});
}

async function clearMemory() {
  if (!confirm("Clear all memory? This cannot be undone.")) return;
  await jarvis.clearHistory();
  alert("Memory cleared.");
}

// ─── Theme ─────────────────────────────────────────────────

async function loadTheme() {
  let theme = "dark";
  try {
    theme = await jarvis.getTheme() || "dark";
  } catch {}
  applyTheme(theme);
  const toggle = document.getElementById("theme-toggle");
  if (toggle) toggle.classList.toggle("on", theme === "dark");
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
}

function toggleTheme(el) {
  el.classList.toggle("on");
  const isDark = el.classList.contains("on");
  const theme = isDark ? "dark" : "light";
  jarvis.setTheme(theme);
  applyTheme(theme);
}

// ─── IPC event listeners ───────────────────────────────────

function setupIpcListeners() {
  jarvis.on("backend-ready", () => {
    setOnline(true);
    fetchStatus();
  });

  jarvis.on("backend-offline", () => setOnline(false));

  jarvis.on("toggle-voice", () => toggleVoice());

  jarvis.on("push-to-talk", () => {
    if (!state.voiceActive) startVoice();
  });

  jarvis.on("theme-changed", (theme) => {
    applyTheme(theme);
  });

  jarvis.on("nav", (view) => switchView(view));
}

// ─── Markdown renderer (lightweight) ──────────────────────

function renderMarkdown(text) {
  return text
    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
    .replace(/```(\w*)\n?([\s\S]*?)```/g, (_, lang, code) =>
      `<pre><code class="lang-${lang}">${code.trim()}</code></pre>`)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    .replace(/^\s*[-*]\s+(.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>")
    .replace(/\n{2,}/g, "<br><br>")
    .replace(/\n/g, "<br>");
}


// ─── Multilingual support ──────────────────────────────────

const LANG_FLAGS = { en: "🇬🇧", ml: "🇮🇳", hi: "🇮🇳", ta: "🇮🇳" };
const LANG_NAMES = { en: "English", ml: "മലയാളം", hi: "हिंदी", ta: "தமிழ்" };

async function loadLanguageBar() {
  try {
    const res = await fetch("http://localhost:7771/language");
    const data = await res.json();
    if (!data || data.error) return;

    const current = data.current || "en";
    const bar = document.getElementById("lang-bar");
    if (!bar) return;

    bar.innerHTML = (data.supported || []).map(l => `
      <button class="lang-btn ${l.code === current ? "active" : ""}"
              onclick="switchLanguage('${l.code}')"
              title="${l.name}">
        ${LANG_FLAGS[l.code] || "🌐"} ${l.native || l.name}
      </button>`).join("");

    document.getElementById("current-lang-label").textContent =
      `${LANG_FLAGS[current]} ${LANG_NAMES[current] || current}`;
  } catch {}
}

async function switchLanguage(code) {
  try {
    await fetch("http://localhost:7771/language/set", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lang: code })
    });
    await loadLanguageBar();
    const names = { en: "English", ml: "Malayalam", hi: "Hindi", ta: "Tamil" };
    appendMessage("assistant",
      `Language switched to **${names[code] || code}**. ` +
      { en: "How may I help you?",
        ml: "എങ്ങനെ സഹായിക്കാൻ കഴിയും?",
        hi: "मैं आपकी कैसे मदद कर सकता हूँ?",
        ta: "உங்களுக்கு எப்படி உதவலாம்?" }[code]
    );
  } catch (e) {
    console.error("Language switch failed:", e);
  }
}

// Auto-detect language as user types and show flag
document.addEventListener("DOMContentLoaded", () => {
  const input = document.getElementById("user-input");
  if (!input) return;
  let detectTimer;
  input.addEventListener("input", () => {
    clearTimeout(detectTimer);
    detectTimer = setTimeout(async () => {
      const text = input.value.trim();
      if (text.length < 4) return;
      try {
        const res = await fetch("http://localhost:7771/language/detect", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text })
        });
        const data = await res.json();
        if (data && data.code) {
          const flag = LANG_FLAGS[data.code] || "🌐";
          const lbl = document.getElementById("input-lang-indicator");
          if (lbl) lbl.textContent = flag;
        }
      } catch {}
    }, 400);
  });

  // Load language bar on startup
  setTimeout(loadLanguageBar, 1500);
});
