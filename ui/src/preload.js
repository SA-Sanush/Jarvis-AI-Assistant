import React from 'react';
  
  const Preload = () =>  {
	return (
	  <div>
	  </div>
	);
  }
  
  export default Preload;
  /**
 * JARVIS Preload — ui/src/preload.js
 * Secure contextBridge between renderer and main process.
 * Renderer can ONLY call these explicitly exposed APIs.
 */

const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("jarvis", {
  // Chat
  chat: (message) => ipcRenderer.invoke("chat", { message }),
  getHistory: () => ipcRenderer.invoke("get-history"),
  clearHistory: () => ipcRenderer.invoke("clear-history"),

  // Voice
  voiceStart: () => ipcRenderer.invoke("voice-start"),
  voiceStop: () => ipcRenderer.invoke("voice-stop"),

  // Status & system
  getStatus: () => ipcRenderer.invoke("get-status"),
  getSystemInfo: () => ipcRenderer.invoke("get-system-info"),

  // Settings
  getSettings: () => ipcRenderer.invoke("settings-get"),
  saveSettings: (s) => ipcRenderer.invoke("settings-save", s),

  // Theme
  getTheme: () => ipcRenderer.invoke("get-theme"),
  setTheme: (t) => ipcRenderer.send("set-theme", t),

  // Window controls
  minimize: () => ipcRenderer.send("window-minimize"),
  maximize: () => ipcRenderer.send("window-maximize"),
  close: () => ipcRenderer.send("window-close"),
  openExternal: (url) => ipcRenderer.send("open-external", url),

  // Event listeners
  on: (channel, cb) => {
    const allowed = [
      "backend-ready", "backend-offline", "backend-log",
      "toggle-voice", "push-to-talk", "theme-changed", "nav"
    ];
    if (allowed.includes(channel)) {
      ipcRenderer.on(channel, (_, ...args) => cb(...args));
    }
  },
  off: (channel) => ipcRenderer.removeAllListeners(channel),
});
