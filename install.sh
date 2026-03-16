#!/usr/bin/env bash
set -euo pipefail

# DrClaw installer / updater / uninstaller
# Usage: bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh) [install|update|uninstall] [--purge-data]

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

usage() {
    cat <<EOF
Usage:
  install.sh [install]
  install.sh update
  install.sh uninstall [--purge-data]

Examples:
  bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh)
  bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh) update
  bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh) uninstall
  bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh) uninstall --purge-data
EOF
}

# --- Config ------------------------------------------------------------
DRCLAW_DIR="${DRCLAW_DIR:-$HOME/.drclaw-src}"
DRCLAW_DATA_DIR="${DRCLAW_DATA_DIR:-$HOME/.drclaw}"
SYMLINK_DIR="$HOME/.local/bin"
REPO_URL="https://github.com/qzzqzzb/drclaw.git"
MIN_PYTHON="3.10"

ACTION="install"
PURGE_DATA=0
POSITIONAL_ACTION_SET=0

for arg in "$@"; do
    case "$arg" in
        install|update|uninstall)
            if [ "$POSITIONAL_ACTION_SET" -eq 1 ]; then
                die "Multiple actions specified. Choose one of: install, update, uninstall."
            fi
            ACTION="$arg"
            POSITIONAL_ACTION_SET=1
            ;;
        --purge-data)
            PURGE_DATA=1
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown argument: $arg"
            ;;
    esac
done

if [ "$ACTION" != "uninstall" ] && [ "$PURGE_DATA" -eq 1 ]; then
    die "--purge-data is only supported with uninstall."
fi

OS=""
ARCH=""

detect_platform() {
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
}

check_python() {
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
}

ensure_uv() {
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
}

ensure_git() {
    command -v git >/dev/null 2>&1 || die "git not found. Install git first."
}

configure_sparse_checkout() {
    mkdir -p "$DRCLAW_DIR/.git/info"
    git -C "$DRCLAW_DIR" config core.sparseCheckout true
    git -C "$DRCLAW_DIR" config core.sparseCheckoutCone false
    cat > "$DRCLAW_DIR/.git/info/sparse-checkout" <<'EOF'
/*
!/assets/demos/
!/assets/demos/**
EOF
}

sync_repo() {
    if [ -d "$DRCLAW_DIR/.git" ]; then
        info "Updating $DRCLAW_DIR..."
        configure_sparse_checkout
        git -C "$DRCLAW_DIR" read-tree -mu HEAD
        git -C "$DRCLAW_DIR" pull --rebase --quiet
        success "Repository updated"
    else
        if [ -d "$DRCLAW_DIR" ]; then
            warn "$DRCLAW_DIR exists but is not a git repo. Removing and re-cloning..."
            rm -rf "$DRCLAW_DIR"
        fi
        info "Cloning DrClaw into $DRCLAW_DIR..."
        git clone --quiet --filter=blob:none --no-checkout "$REPO_URL" "$DRCLAW_DIR"
        configure_sparse_checkout
        git -C "$DRCLAW_DIR" checkout --quiet HEAD
        success "Repository cloned"
    fi
}

install_dependencies() {
    info "Installing dependencies (this may take a minute)..."
    cd "$DRCLAW_DIR"
    if [ "$OS" = "Darwin" ]; then
        uv sync --extra tray --quiet
    else
        uv sync --quiet
    fi
    success "Dependencies installed"
}

symlink_binary() {
    DRCLAW_BIN="$DRCLAW_DIR/.venv/bin/drclaw"

    if [ ! -f "$DRCLAW_BIN" ]; then
        die "Expected binary not found at $DRCLAW_BIN. Installation may have failed."
    fi

    mkdir -p "$SYMLINK_DIR"
    ln -sf "$DRCLAW_BIN" "$SYMLINK_DIR/drclaw"
    success "Symlinked drclaw -> $SYMLINK_DIR/drclaw"

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
        export PATH="$SYMLINK_DIR:$PATH"
    fi
}

run_onboard() {
    info "Running onboard..."
    if drclaw onboard 2>/dev/null; then
        success "Onboard complete"
    else
        warn "Onboard skipped (command may not exist yet — run 'drclaw onboard' manually)"
    fi
}

print_install_summary() {
    local label="$1"

    echo ""
    echo "${BOLD}${GREEN}DrClaw ${label}!${RESET}"
    echo ""
    echo "  Source:  $DRCLAW_DIR"
    echo "  Binary:  $SYMLINK_DIR/drclaw"
    echo "  Config:  $DRCLAW_DATA_DIR/config.json"
    echo ""
    echo "${BOLD}Next steps:${RESET}"
    echo "  1. Set your API key in ${BOLD}$DRCLAW_DATA_DIR/config.json${RESET}"
    echo "  2. Launch DrClaw:     ${BOLD}drclaw daemon -f web${RESET}"
    echo ""
}

best_effort_uninstall_launchd() {
    if [ "$OS" != "Darwin" ]; then
        return
    fi

    if [ -x "$DRCLAW_DIR/.venv/bin/drclaw" ]; then
        info "Stopping macOS LaunchAgent (if installed)..."
        if "$DRCLAW_DIR/.venv/bin/drclaw" launchd uninstall >/dev/null 2>&1; then
            success "LaunchAgent removed"
        else
            warn "LaunchAgent uninstall skipped"
        fi
        return
    fi

    if command -v drclaw >/dev/null 2>&1; then
        info "Stopping macOS LaunchAgent (if installed)..."
        if drclaw launchd uninstall >/dev/null 2>&1; then
            success "LaunchAgent removed"
        else
            warn "LaunchAgent uninstall skipped"
        fi
    fi
}

do_install_or_update() {
    detect_platform
    check_python
    ensure_uv
    ensure_git

    if [ "$ACTION" = "update" ] && [ ! -d "$DRCLAW_DIR/.git" ]; then
        warn "Existing installation not found at $DRCLAW_DIR. Proceeding with a fresh install."
    fi

    sync_repo
    install_dependencies
    symlink_binary
    run_onboard

    if [ "$ACTION" = "update" ]; then
        print_install_summary "updated"
    else
        print_install_summary "installed"
    fi
}

do_uninstall() {
    detect_platform
    best_effort_uninstall_launchd

    if [ -L "$SYMLINK_DIR/drclaw" ] || [ -e "$SYMLINK_DIR/drclaw" ]; then
        info "Removing $SYMLINK_DIR/drclaw..."
        rm -f "$SYMLINK_DIR/drclaw"
        success "Removed binary symlink"
    else
        warn "Binary symlink not found at $SYMLINK_DIR/drclaw"
    fi

    if [ -d "$DRCLAW_DIR" ]; then
        info "Removing $DRCLAW_DIR..."
        rm -rf "$DRCLAW_DIR"
        success "Removed source directory"
    else
        warn "Source directory not found at $DRCLAW_DIR"
    fi

    if [ "$PURGE_DATA" -eq 1 ]; then
        if [ -d "$DRCLAW_DATA_DIR" ]; then
            info "Removing $DRCLAW_DATA_DIR..."
            rm -rf "$DRCLAW_DATA_DIR"
            success "Removed data directory"
        else
            warn "Data directory not found at $DRCLAW_DATA_DIR"
        fi
    fi

    echo ""
    echo "${BOLD}${GREEN}DrClaw uninstalled!${RESET}"
    echo ""
    echo "  Removed source:  $DRCLAW_DIR"
    echo "  Removed binary:  $SYMLINK_DIR/drclaw"
    if [ "$PURGE_DATA" -eq 1 ]; then
        echo "  Removed data:    $DRCLAW_DATA_DIR"
    else
        echo "  Preserved data:  $DRCLAW_DATA_DIR"
        echo "  To remove all local data too, run:"
        echo "    ${BOLD}bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh) uninstall --purge-data${RESET}"
    fi
    echo ""
}

case "$ACTION" in
    install|update)
        do_install_or_update
        ;;
    uninstall)
        do_uninstall
        ;;
    *)
        die "Unsupported action: $ACTION"
        ;;
esac
