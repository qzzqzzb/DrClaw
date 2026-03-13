#!/bin/bash
# Claude Code Skill Installation Script

echo "========================================"
echo "   Claude Code Skill for OpenClaw"
echo "========================================"
echo ""

# Check if OpenClaw is installed
if ! command -v openclaw &> /dev/null; then
    echo "❌ Error: OpenClaw is not installed"
    echo "Please install OpenClaw first: https://openclaw.ai"
    exit 1
fi

echo "✅ OpenClaw detected"

# Create skill directory
SKILL_DIR="$HOME/.openclaw/skills/claude-code"
mkdir -p "$SKILL_DIR"

echo "📁 Creating skill directory: $SKILL_DIR"

# Copy skill files
cp claude-code.py "$SKILL_DIR/"
cp README.md "$SKILL_DIR/"

# Make executable
chmod +x "$SKILL_DIR/claude-code.py"

echo "✅ Skill files installed"

# Verify installation
if [ -f "$SKILL_DIR/claude-code.py" ]; then
    echo "✅ Installation successful!"
    echo ""
    echo "========================================"
    echo "   Usage Instructions"
    echo "========================================"
    echo ""
    echo "1. View documentation overview:"
    echo "   claude-code docs"
    echo ""
    echo "2. Query specific topic:"
    echo "   claude-code query quickstart"
    echo "   claude-code query best-practices"
    echo "   claude-code query troubleshooting"
    echo ""
    echo "3. Get help:"
    echo "   claude-code --help"
    echo ""
    echo "========================================"
    echo ""
    echo "📚 Documentation: https://code.claude.com/docs"
    echo "🌐 OpenClaw: https://openclaw.ai"
    echo ""
else
    echo "❌ Installation failed"
    exit 1
fi
