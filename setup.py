#!/usr/bin/env python3
"""
JARVIS Setup Script
Works on Windows and Fedora Linux.
"""

import sys
import platform
import subprocess
import os
import shutil
from pathlib import Path

OS = platform.system()
PYTHON = sys.executable

def run(cmd: list, check=True):
    print(f"  → {' '.join(cmd)}")
    subprocess.run(cmd, check=check)

def banner(text: str):
    print(f"\n{'─'*50}")
    print(f"  {text}")
    print(f"{'─'*50}")

def check_python():
    banner("Checking Python version")
    v = sys.version_info
    if v < (3, 11):
        print(f"❌ Python 3.11+ required. You have {v.major}.{v.minor}")
        sys.exit(1)
    print(f"✅ Python {v.major}.{v.minor}.{v.micro}")

def install_system_deps():
    banner(f"Installing system dependencies ({OS})")
    if OS == "Linux":
        # Fedora/RHEL
        pkgs = ["portaudio-devel", "python3-devel", "gcc", "ffmpeg"]
        try:
            run(["sudo", "dnf", "install", "-y"] + pkgs)
        except Exception:
            # Ubuntu/Debian fallback
            run(["sudo", "apt-get", "install", "-y",
                 "portaudio19-dev", "python3-dev", "gcc", "ffmpeg"], check=False)
    elif OS == "Windows":
        # Check for chocolatey
        try:
            run(["choco", "install", "ffmpeg", "-y"], check=False)
        except FileNotFoundError:
            print("  ℹ  Install FFmpeg manually from https://ffmpeg.org/download.html")

def install_python_deps():
    banner("Installing Python dependencies")
    run([PYTHON, "-m", "pip", "install", "--upgrade", "pip"])
    run([PYTHON, "-m", "pip", "install", "-r", "requirements.txt"])

def setup_ollama():
    banner("Setting up Ollama (local AI)")
    print("  Ollama lets JARVIS run fully offline.")
    if OS == "Linux":
        print("  Install with: curl -fsSL https://ollama.com/install.sh | sh")
        print("  Then run: ollama pull llama3.2")
    elif OS == "Windows":
        print("  Download from: https://ollama.com/download/windows")
        print("  Then run: ollama pull llama3.2")

def create_env_file():
    banner("Creating .env template")
    env_path = Path(".env")
    if env_path.exists():
        print("  .env already exists, skipping.")
        return
    template_path = Path(".env.example")
    if template_path.exists():
        shutil.copyfile(template_path, env_path)
    else:
        env_path.write_text("# JARVIS API keys\n")
    print(f"  ✅ Created .env — add your API keys there.")

def create_memory_dir():
    banner("Creating JARVIS data directories")
    dirs = [
        Path.home() / ".jarvis" / "memory",
        Path.home() / ".jarvis" / "logs",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  ✅ {d}")

def main():
    print("\n" + "═"*50)
    print("  🤖  JARVIS Setup Wizard")
    print("═"*50)
    print(f"  OS: {OS} {platform.release()}")

    check_python()
    install_system_deps()
    install_python_deps()
    setup_ollama()
    create_env_file()
    create_memory_dir()

    print("\n" + "═"*50)
    print("  ✅  JARVIS setup complete!")
    print("═"*50)
    print("\n  Next steps:")
    print("  1. Add your API keys to .env")
    print("  2. (Optional) Install Ollama for offline AI")
    if OS == "Windows":
        print("  3. Run: launch.bat")
    else:
        print("  3. Run: ./launch.sh")
    print()

if __name__ == "__main__":
    main()
