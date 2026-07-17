#!/usr/bin/env bash
# One-shot setup for macOS + Linux. Installs the toolchain (git, Rust, uv,
# Node + pnpm, the Tauri system libraries on Linux), then all project
# dependencies, including the local Chromium the app's browser features use.
# Safe to re-run — every step is idempotent.
#
#   curl -fsSL https://raw.githubusercontent.com/SrinivasRavi/finds-you-jobs/main/scripts/setup.sh | bash
# or, if you already cloned the repo:
#   bash scripts/setup.sh
set -euo pipefail

REPO_URL="https://github.com/SrinivasRavi/finds-you-jobs.git"
BLUE=$'\033[1;34m'; GREEN=$'\033[1;32m'; NC=$'\033[0m'
step() { echo; echo "${BLUE}==> $1${NC}"; }
have() { command -v "$1" >/dev/null 2>&1; }

# --- 0. git (needed to clone) ------------------------------------------------
step "Checking git"
if ! have git; then
  echo "git is not installed."
  case "$(uname -s)" in
    Darwin) echo "Installing the Xcode command-line tools (includes git)…"
            echo "Accept the popup, wait for it to finish, then RE-RUN this script."
            xcode-select --install || true; exit 1 ;;
    Linux)  if have apt-get; then sudo apt-get update && sudo apt-get install -y git
            elif have dnf; then sudo dnf install -y git
            elif have pacman; then sudo pacman -Sy --noconfirm git
            else echo "Install git with your package manager, then re-run."; exit 1; fi ;;
  esac
fi
echo "git $(git --version | awk '{print $3}')"

# --- 1. Linux only: the Tauri desktop-shell system libraries ------------------
if [ "$(uname -s)" = "Linux" ]; then
  step "Linux system libraries for the desktop shell"
  if have apt-get; then
    sudo apt-get update
    sudo apt-get install -y build-essential curl wget file libssl-dev \
      libwebkit2gtk-4.1-dev libayatana-appindicator3-dev librsvg2-dev \
      libgtk-3-dev patchelf
  elif have dnf; then
    sudo dnf install -y gcc gcc-c++ make curl wget file openssl-devel \
      webkit2gtk4.1-devel libappindicator-gtk3-devel librsvg2-devel \
      gtk3-devel patchelf
  elif have pacman; then
    sudo pacman -Sy --noconfirm base-devel curl wget file openssl \
      webkit2gtk-4.1 libappindicator-gtk3 librsvg gtk3 patchelf
  else
    echo "Unknown package manager — install the Tauri v2 Linux prerequisites"
    echo "manually (https://v2.tauri.app/start/prerequisites/), then re-run."
    exit 1
  fi
fi

# --- 2. clone or update the repo ---------------------------------------------
step "Getting the code"
if [ -f "package.json" ] && grep -q '"name": "finds-you-jobs"' package.json 2>/dev/null; then
  echo "Already inside the repo — pulling latest…"; git pull --ff-only || true
elif [ -d "finds-you-jobs/.git" ]; then
  cd finds-you-jobs; echo "Found existing clone — pulling latest…"; git pull --ff-only || true
else
  git clone "$REPO_URL"; cd finds-you-jobs
fi

# --- 3. Rust (Tauri shell) ---------------------------------------------------
step "Rust toolchain (for the desktop shell)"
if ! have cargo; then
  curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
  # shellcheck disable=SC1090,SC1091
  source "$HOME/.cargo/env"
fi
echo "cargo $(cargo --version | awk '{print $2}')"

# --- 4. uv (Python 3.13 + the sidecar deps) ----------------------------------
step "uv (Python package manager)"
if ! have uv; then curl -LsSf https://astral.sh/uv/install.sh | sh; export PATH="$HOME/.local/bin:$PATH"; fi
echo "uv $(uv --version | awk '{print $2}')"

# --- 5. Node + pnpm (frontend) -----------------------------------------------
step "Node + pnpm (for the UI)"
if ! have node; then
  case "$(uname -s)" in
    Darwin) if have brew; then brew install node; else
            echo "Install Node LTS from https://nodejs.org, re-open Terminal, re-run this script."; exit 1; fi ;;
    Linux)  if have apt-get; then
              curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - && sudo apt-get install -y nodejs
            else
              echo "Install Node LTS from https://nodejs.org (or your package manager), then re-run."; exit 1
            fi ;;
  esac
fi
if ! have pnpm; then corepack enable pnpm 2>/dev/null || npm install -g pnpm; fi
echo "node $(node --version)  ·  pnpm $(pnpm --version)"

# --- 6. project dependencies -------------------------------------------------
step "Installing project dependencies"
pnpm run boot

# --- 7. local Chromium (the Applier / PDF export drive a real browser) --------
step "Downloading the app's local Chromium (one-time, ~150 MB)"
uv run playwright install chromium

# When piped via `curl | bash` the script's cd cannot change the user's own
# shell — print the full path so the next command actually works (2026-07-17).
echo
echo "${GREEN}Done.${NC} Start the app with:"
echo "    cd $(pwd) && pnpm dev"
