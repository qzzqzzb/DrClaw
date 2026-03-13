#!/usr/bin/env bash
set -euo pipefail

# DrClaw installer
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/XXXXXX/install.sh)

# --- Colors -----------------------------------------------------------
if [ -t 1 ] && command -v tput >/dev/null 2>&1; then
    BOLD=$(tput bold)
    RED=$(tput setaf 1)
    GREEN=$(tput setaf 2)
    YELLOW=$(tput setaf 3)
    BLUE=$(tput setaf 4)
    RESET=$(tput sgr0)
else
    BOLD="" RED="" GREEN="" YELLOW="" BLUE="" RESET=""
fi

info()    { printf '%s[info]%s %s\n'    "$BLUE"   "$RESET" "$*"; }
success() { printf '%s[ok]%s   %s\n'    "$GREEN"  "$RESET" "$*"; }
warn()    { printf '%s[warn]%s %s\n'    "$YELLOW" "$RESET" "$*"; }
die()     { printf '%s[error]%s %s\n'   "$RED"    "$RESET" "$*" >&2; exit 1; }

# --- Config ------------------------------------------------------------
DRCLAW_DIR="${DRCLAW_DIR:-$HOME/.drclaw-src}"
SYMLINK_DIR="$HOME/.local/bin"
REPO_URL="https://github.com/qzzqzzb/drclaw.git"
MIN_PYTHON="3.10"

# --- Pre-flight --------------------------------------------------------
info "Detecting platform..."

OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Darwin) info "macOS $ARCH" ;;
    Linux)  info "Linux $ARCH" ;;
    MINGW*|MSYS*|CYGWIN*)
        die "Windows detected. Please use WSL: https://learn.microsoft.com/en-us/windows/wsl/install"
        ;;
    *) die "Unsupported OS: $OS" ;;
esac

# --- Check Python >= 3.10 ---------------------------------------------
info "Checking Python..."

PYTHON=""
for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1; then
        PYTHON="$candidate"
        break
    fi
done

if [ -z "$PYTHON" ]; then
    die "Python not found. Install Python >= $MIN_PYTHON:
  macOS:  brew install python@3.12
  Ubuntu: sudo apt install python3
  Fedora: sudo dnf install python3"
fi

PY_VERSION=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PY_MAJOR=$("$PYTHON" -c 'import sys; print(sys.version_info.major)')
PY_MINOR=$("$PYTHON" -c 'import sys; print(sys.version_info.minor)')

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 10 ]; }; then
    die "Python $PY_VERSION found, but >= $MIN_PYTHON required.
  macOS:  brew install python@3.12
  Ubuntu: sudo apt install python3.12"
fi

success "Python $PY_VERSION"

# --- Install uv (if missing) ------------------------------------------
if ! command -v uv >/dev/null 2>&1; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh

    # Source uv into current shell
    if [ -f "$HOME/.local/bin/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.local/bin/env"
    elif [ -f "$HOME/.cargo/env" ]; then
        # shellcheck disable=SC1091
        . "$HOME/.cargo/env"
    fi

    command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH. Restart your shell and re-run."
    success "uv installed"
else
    success "uv $(uv --version 2>/dev/null || echo '(found)')"
fi

# --- Check git ---------------------------------------------------------
command -v git >/dev/null 2>&1 || die "git not found. Install git first."

# --- Clone / update repo ----------------------------------------------
if [ -d "$DRCLAW_DIR/.git" ]; then
    info "Updating $DRCLAW_DIR..."
    git -C "$DRCLAW_DIR" pull --rebase --quiet
    success "Repository updated"
else
    if [ -d "$DRCLAW_DIR" ]; then
        warn "$DRCLAW_DIR exists but is not a git repo. Removing and re-cloning..."
        rm -rf "$DRCLAW_DIR"
    fi
    info "Cloning DrClaw into $DRCLAW_DIR..."
    git clone --quiet "$REPO_URL" "$DRCLAW_DIR"
    success "Repository cloned"
fi

# --- Create venv + install ---------------------------------------------
info "Installing dependencies (this may take a minute)..."
cd "$DRCLAW_DIR"
uv sync --extra tray --quiet
success "Dependencies installed"

# --- Symlink to PATH ---------------------------------------------------
DRCLAW_BIN="$DRCLAW_DIR/.venv/bin/drclaw"

if [ ! -f "$DRCLAW_BIN" ]; then
    die "Expected binary not found at $DRCLAW_BIN. Installation may have failed."
fi

mkdir -p "$SYMLINK_DIR"
ln -sf "$DRCLAW_BIN" "$SYMLINK_DIR/drclaw"
success "Symlinked drclaw -> $SYMLINK_DIR/drclaw"

# Check if ~/.local/bin is in PATH
if ! echo "$PATH" | tr ':' '\n' | grep -qx "$SYMLINK_DIR"; then
    SHELL_NAME="$(basename "${SHELL:-/bin/bash}")"
    case "$SHELL_NAME" in
        zsh)  RC_FILE="~/.zshrc" ;;
        bash) RC_FILE="~/.bashrc" ;;
        fish) RC_FILE="~/.config/fish/config.fish" ;;
        *)    RC_FILE="your shell rc file" ;;
    esac
    warn "$SYMLINK_DIR is not in your PATH. Add it:"
    if [ "$SHELL_NAME" = "fish" ]; then
        echo "  ${BOLD}fish_add_path $SYMLINK_DIR${RESET}"
    else
        echo "  ${BOLD}echo 'export PATH=\"$SYMLINK_DIR:\$PATH\"' >> $RC_FILE${RESET}"
    fi
    echo ""
    # Make drclaw available for the onboard step below
    export PATH="$SYMLINK_DIR:$PATH"
fi

# --- Onboard -----------------------------------------------------------
info "Running onboard..."
if drclaw onboard 2>/dev/null; then
    success "Onboard complete"
else
    warn "Onboard skipped (command may not exist yet — run 'drclaw onboard' manually)"
fi

# --- Summary -----------------------------------------------------------
echo ""
echo "${BOLD}${GREEN}DrClaw installed!${RESET}"
echo ""
echo "  Source:  $DRCLAW_DIR"
echo "  Binary:  $SYMLINK_DIR/drclaw"
echo "  Config:  ~/.drclaw/config.json"
echo ""
echo "${BOLD}Next steps:${RESET}"
echo "  1. Set your API key in ${BOLD}~/.drclaw/config.json${RESET}"
if [ "$OS" = "Darwin" ]; then
echo "  2. Launch DrClaw:     ${BOLD}drclaw tray${RESET}"
else
echo "  2. Launch DrClaw:     ${BOLD}drclaw daemon -f web${RESET}"
fi
echo ""
