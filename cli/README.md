# @hams-ai/cli

Official CLI for [hams.ai](https://hams.ai) — AI Coding Agent.

## Install

```powershell
npm install -g @hams-ai/cli
```

## Prerequisites

- **Node.js** 16+ — [nodejs.org](https://nodejs.org)
- **Python** 3.8+ — [python.org](https://python.org)
- **hams.ai** project folder (the Python agent)

## Setup

After install, tell the CLI where your hams.ai project lives:

**PowerShell:**
```powershell
$env:HAMS_PATH = "C:\Users\kamu\hams.ai"
```

**Linux / Mac:**
```bash
export HAMS_PATH="/home/kamu/hams.ai"
```

To make it permanent, add the line above to your shell profile (`$PROFILE` on PowerShell, `~/.bashrc` or `~/.zshrc` on Linux/Mac).

## Usage

```powershell
# Interactive chat (default)
hams

# Run a single task
hams run "buatkan REST API dengan FastAPI untuk CRUD user"

# List available tools
hams tools

# Check if backend is running
hams status

# Show Python backend output (debug)
hams --verbose

# Use custom port
hams --port 9000
```

## How it works

```
hams (CLI)
  └── finds Python on your system
  └── auto-installs requirements.txt (first time)
  └── spawns agent/api.py --port 8000
  └── waits for /health to respond
  └── sends your tasks via POST /run/stream
  └── streams response back to terminal
  └── shuts down Python when you exit
```

## Development (local install)

```powershell
# Di dalam folder cli/
npm install
npm link

# Sekarang "hams" tersedia di terminal
hams
```

## Publishing

```powershell
npm login
npm publish --access public
```
