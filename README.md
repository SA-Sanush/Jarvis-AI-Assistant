# J.A.R.V.I.S — AI Desktop Assistant

> Just A Rather Very Intelligent System  
> Cross-platform AI assistant for **Windows** and **Fedora Linux**

---

## Features

| Module | Capability |
|--------|-----------|
| 🧠 AI Brain | 9 providers with auto-fallback: Ollama · Claude · GPT-4o · Groq · Gemini · Mistral · Cohere · Together · HuggingFace |
| 🎙️ Voice | Wake word ("Jarvis") → Whisper STT → streaming TTS · Works 100% offline |
| 🖥️ PC Control | Open apps · manage files · system info · volume · screenshots · web search |
| 💾 Memory | ChromaDB vector memory — remembers conversations across sessions |
| 🌐 Web Search | Tavily · Brave · Serper · DuckDuckGo with auto-fallback |
| 🖼️ UI | Electron desktop app — arc reactor dark theme · system tray · global shortcuts |

---

## Quick Start

### 1 — Clone & install

```bash
git clone https://github.com/your-repo/jarvis
cd jarvis

# Python dependencies
pip install -r requirements.txt

# UI dependencies
cd ui && npm install && cd ..
```

### 2 — Configure API keys

```bash
cp .env.example .env
# Edit .env and add your keys
```

> **Minimum setup:** Install [Ollama](https://ollama.com) + run `ollama pull llama3.2` — no API keys needed.

### 3 — Launch

| Platform | Command |
|----------|---------|
| Windows (UI) | Double-click `launch.bat` |
| Linux (UI) | `./launch.sh` |
| Voice mode | `./launch.sh voice` |
| Text CLI | `./launch.sh text` |
| Push-to-talk | `./launch.sh ptt` |

---

## Global Shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+Shift+J` | Show/hide JARVIS |
| `Ctrl+Shift+V` | Toggle voice mode |
| `Ctrl+Shift+Space` | Push-to-talk |

---

## Voice Commands (examples)

```
"Open Spotify"                    → launches Spotify
"Google quantum computing"        → opens browser search
"Find my resume"                  → searches filesystem
"Organize my downloads"           → auto-sorts by file type
"Take a screenshot"               → saves to Pictures/
"Set volume to 60"                → system volume
"What's my CPU usage?"            → live system info
"Lock the screen"                 → locks workstation
"Run command: ls -la"             → executes terminal command
```

---

## Project Structure

```
jarvis/
├── core/
│   ├── brain.py        # AI router (9 providers + fallback)
│   ├── memory.py       # ChromaDB vector memory
│   ├── search.py       # Web search (4 providers)
│   └── jarvis.py       # Main orchestrator
├── voice/
│   ├── wake_word.py    # Porcupine / Vosk / fallback
│   ├── stt.py          # Whisper / DeepGram / AssemblyAI
│   ├── tts.py          # Coqui / ElevenLabs / gTTS / pyttsx3
│   └── pipeline.py     # Full voice loop
├── skills/
│   ├── pc_control.py   # NL → OS actions
│   ├── file_manager.py # File search, organize, watch
│   └── os_layer.py     # Windows / Linux abstraction
├── ui/
│   ├── src/main.js     # Electron main process
│   ├── src/preload.js  # Secure IPC bridge
│   ├── src/index.html  # UI layout
│   ├── src/renderer.js # UI logic
│   └── styles/         # CSS (dark arc reactor theme)
├── config/
│   └── settings.yaml   # All configuration
├── server.py           # Python HTTP bridge for Electron
├── main.py             # CLI entry point
├── launch.sh           # Linux launcher
├── launch.bat          # Windows launcher
└── .env.example        # API key template
```

---

## AI Provider Priority

JARVIS tries providers in this order until one succeeds:

1. **Ollama** (local, offline, free) — install from [ollama.com](https://ollama.com)
2. **Anthropic Claude** — best reasoning quality
3. **OpenAI GPT-4o** — strong general purpose
4. **Groq** — fastest inference (free tier available)
5. **Google Gemini** — multimodal
6. **Mistral** — efficient European model
7. **Cohere** — strong for RAG/search
8. **Together AI** — many open models
9. **HuggingFace** — largest model selection

---

## Requirements

- Python 3.11+
- Node.js 18+ (for UI only)
- FFmpeg (for audio)
- Optional: [Ollama](https://ollama.com) for offline AI

### Fedora setup
```bash
sudo dnf install python3 nodejs ffmpeg portaudio-devel
```

### Windows setup
```bash
# Install Python from python.org, Node.js from nodejs.org
# Install FFmpeg via chocolatey:
choco install ffmpeg
```
