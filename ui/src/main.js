import React from 'react';
  
  const Main = () =>  {
	return (
	  <div>
	  </div>
	);
  }
  
  export default Main;
  /**
 * JARVIS Electron Main Process — ui/src/main.js
 * Manages the app window, bridges to Python backend via IPC,
 * handles system tray, global shortcuts, and auto-launch.
 */

const { app, BrowserWindow, ipcMain, Tray, Menu, globalShortcut, nativeTheme, shell, nativeImage } = require("electron");
const path = require("path");
const fs = require("fs");
const { spawn } = require("child_process");
const net = require("net");
const os = require("os");

// ─── Config ────────────────────────────────────────────────
const IS_DEV = process.env.NODE_ENV === "development";
const IS_WIN = process.platform === "win32";
const IS_LINUX = process.platform === "linux";
const PYTHON = IS_WIN ? "python" : "python3";
const BACKEND_PORT = 7771;
const BACKEND_SCRIPT = path.join(__dirname, "..", "..", "server.py");
const FALLBACK_ICON = "data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='64' height='64' viewBox='0 0 64 64'><rect width='64' height='64' rx='14' fill='%230b1117'/><circle cx='32' cy='32' r='18' fill='none' stroke='%2300d2ff' stroke-width='4'/><circle cx='32' cy='32' r='5' fill='%2300d2ff'/></svg>";

// ─── State ─────────────────────────────────────────────────
let mainWindow = null;
let tray = null;
let pythonProcess = null;
let backendReady = false;

function getAssetImage(fileName) {
  const iconPath = path.join(__dirname, "..", "assets", fileName);
  return fs.existsSync(iconPath)
    ? iconPath
    : nativeImage.createFromDataURL(FALLBACK_ICON);
}

// ─── Window creation ───────────────────────────────────────

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 900,
    height: 680,
    minWidth: 600,
    minHeight: 500,
    frame: false,               // Custom title bar
    transparent: false,
    vibrancy: "under-window",
    backgroundColor: nativeTheme.shouldUseDarkColors ? "#0D0D0F" : "#F5F4F0",
    icon: getAssetImage(IS_WIN ? "icon.ico" : "icon.png"),
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false,
    },
    show: false,  // Show after content loads
  });

  mainWindow.loadFile(path.join(__dirname, "index.html"));

  mainWindow.once("ready-to-show", () => {
    mainWindow.show();
    if (IS_DEV) mainWindow.webContents.openDevTools({ mode: "detach" });
  });

  mainWindow.on("close", (e) => {
    if (!app.isQuiting) {
      e.preventDefault();
      mainWindow.hide();   // Keep running in tray
    }
  });

  mainWindow.on("closed", () => { mainWindow = null; });

  // Theme change
  nativeTheme.on("updated", () => {
    mainWindow?.webContents.send("theme-changed", nativeTheme.shouldUseDarkColors ? "dark" : "light");
  });
}

// ─── System tray ───────────────────────────────────────────

function createTray() {
  tray = new Tray(getAssetImage(IS_WIN ? "tray.ico" : "tray.png"));
  tray.setToolTip("JARVIS AI Assistant");

  const menu = Menu.buildFromTemplate([
    { label: "Open JARVIS", click: () => showWindow() },
    { label: "Voice Mode", click: () => toggleVoice() },
    { type: "separator" },
    { label: "Settings", click: () => mainWindow?.webContents.send("nav", "settings") },
    { type: "separator" },
    { label: "Quit", click: () => { app.isQuiting = true; app.quit(); } },
  ]);

  tray.setContextMenu(menu);
  tray.on("click", () => showWindow());
  tray.on("double-click", () => showWindow());
}

function showWindow() {
  if (!mainWindow) createWindow();
  mainWindow.show();
  mainWindow.focus();
}

function toggleVoice() {
  mainWindow?.webContents.send("toggle-voice");
}

// ─── Global shortcuts ──────────────────────────────────────

function registerShortcuts() {
  // Ctrl+Shift+J — show/hide JARVIS
  globalShortcut.register("CommandOrControl+Shift+J", () => {
    if (mainWindow?.isVisible()) mainWindow.hide();
    else showWindow();
  });

  // Ctrl+Shift+V — toggle voice
  globalShortcut.register("CommandOrControl+Shift+V", () => {
    toggleVoice();
    showWindow();
  });

  // Ctrl+Shift+Space — push-to-talk
  globalShortcut.register("CommandOrControl+Shift+Space", () => {
    mainWindow?.webContents.send("push-to-talk");
    showWindow();
  });
}

// ─── Python backend bridge ─────────────────────────────────

function startPythonBackend() {
  const args = ["server.py", "--port", BACKEND_PORT.toString()];
  const cwd = path.join(__dirname, "..", "..");

  pythonProcess = spawn(PYTHON, args, {
    cwd,
    stdio: ["pipe", "pipe", "pipe"],
    env: { ...process.env }
  });

  pythonProcess.stdout.on("data", (data) => {
    const msg = data.toString().trim();
    console.log("[Python]", msg);
    if (msg.includes("JARVIS server ready")) {
      backendReady = true;
      mainWindow?.webContents.send("backend-ready");
    }
  });

  pythonProcess.stderr.on("data", (data) => {
    console.error("[Python Error]", data.toString());
    mainWindow?.webContents.send("backend-log", { level: "error", msg: data.toString() });
  });

  pythonProcess.on("close", (code) => {
    console.log(`Python backend exited with code ${code}`);
    backendReady = false;
    mainWindow?.webContents.send("backend-offline");
  });
}

function stopPythonBackend() {
  if (pythonProcess) {
    pythonProcess.kill();
    pythonProcess = null;
  }
}

// ─── HTTP helper (renderer → Python) ──────────────────────

async function callBackend(endpoint, data = null, method = "POST") {
  try {
    const options = {
      method,
      headers: { "Content-Type": "application/json" },
      signal: AbortSignal.timeout(60000)
    };

    if (data !== null) {
      options.body = JSON.stringify(data);
    }

    const res = await fetch(
      `http://localhost:${BACKEND_PORT}${endpoint}`,
      options
    );

    return await res.json();
  } catch (e) {
    return { error: e.message, success: false };
  }
}

// ─── IPC handlers ──────────────────────────────────────────

ipcMain.handle("chat", async (_, { message }) => {
  return callBackend("/chat", { message });
});

ipcMain.handle("voice-start", async () => {
  return callBackend("/voice/start");
});

ipcMain.handle("voice-stop", async () => {
  return callBackend("/voice/stop");
});

ipcMain.handle("get-status", async () => {
  return callBackend("/status", null, "GET");
});

ipcMain.handle("get-history", async () => {
  return callBackend("/history", null, "GET");
});

ipcMain.handle("clear-history", async () => {
  return callBackend("/history/clear");
});

ipcMain.handle("settings-get", async () => {
  return callBackend("/settings", null, "GET");
});

ipcMain.handle("settings-save", async (_, settings) => {
  return callBackend("/settings/save", settings);
});

ipcMain.handle("get-system-info", async () => {
  return callBackend("/system/info", null, "GET");
});

ipcMain.handle("get-theme", async () => {
  return nativeTheme.shouldUseDarkColors ? "dark" : "light";
});

// Window controls (custom title bar)
ipcMain.on("window-minimize", () => mainWindow?.minimize());
ipcMain.on("window-maximize", () => {
  if (mainWindow?.isMaximized()) mainWindow.unmaximize();
  else mainWindow?.maximize();
});
ipcMain.on("window-close", () => mainWindow?.hide());
ipcMain.on("window-drag", (_, { dx, dy }) => {
  const [x, y] = mainWindow?.getPosition() || [0, 0];
  mainWindow?.setPosition(x + dx, y + dy);
});
ipcMain.on("open-external", (_, url) => shell.openExternal(url));
ipcMain.on("set-theme", (_, theme) => {
  nativeTheme.themeSource = theme;
});

// ─── App lifecycle ─────────────────────────────────────────

app.whenReady().then(() => {
  createWindow();
  createTray();
  registerShortcuts();
  startPythonBackend();

  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
    else showWindow();
  });
});

app.on("will-quit", () => {
  globalShortcut.unregisterAll();
  stopPythonBackend();
});

app.on("window-all-closed", () => {
  // Keep running on tray on Linux/Windows
  if (process.platform === "darwin") app.quit();
});

// Prevent multiple instances
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on("second-instance", () => showWindow());
}
