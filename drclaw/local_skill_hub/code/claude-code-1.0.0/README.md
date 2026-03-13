# Claude Code Skill for OpenClaw

A comprehensive skill integration that provides Claude Code documentation, best practices, and workflow guidance directly within OpenClaw.

## 📋 Table of Contents

- [Overview](#overview)
- [Installation](#installation)
- [Usage](#usage)
- [Commands](#commands)
- [Examples](#examples)
- [Integration](#integration)
- [Contributing](#contributing)
- [License](#license)

## Overview

This skill bridges Claude Code's powerful AI-assisted development capabilities with OpenClaw's workflow system, providing:

- 📚 **Documentation Queries** - Instant access to Claude Code docs
- 🤖 **Best Practices** - AI coding guidelines and workflows
- 🛠️ **Troubleshooting** - Common issues and solutions
- 📖 **Workflow Guidance** - Development process recommendations

## Installation

### Quick Install

```bash
cd /Users/milier/.openclaw/workspace/skills/claude-code
chmod +x install.sh
./install.sh
```

### Manual Install

```bash
# Create skill directory
mkdir -p ~/.openclaw/skills/claude-code

# Copy files
cp claude-code.py ~/.openclaw/skills/claude-code/
cp README.md ~/.openclaw/skills/claude-code/

# Make executable
chmod +x ~/.openclaw/skills/claude-code/claude-code.py
```

### OpenClaw Skill Registry (Future)

Once published to ClawHub, install with:

```bash
clawhub install claude-code
```

## Usage

### Basic Commands

```bash
# View all documentation
claude-code docs

# Query specific topic
claude-code query quickstart
claude-code query best-practices
claude-code query troubleshooting

# Get help
claude-code --help
```

### Integration with OpenClaw

This skill is designed to work seamlessly with OpenClaw's native capabilities:

```bash
# Use with OpenClaw's exec tool
openclaw exec --command "claude-code query best-practices"

# Or call directly from OpenClaw
claude-code query "API design best practices"
```

## Commands

### 📚 docs - Documentation Overview

Show overview of all available documentation.

```bash
claude-code docs                    # Show all topics
claude-code docs quickstart        # Get quickstart guide
claude-code docs best-practices    # AI coding best practices
claude-code docs troubleshooting   # Troubleshooting guide
```

### 🔍 query - Documentation Query

Query specific documentation topics.

```bash
# Query specific topics
claude-code query quickstart
claude-code query best-practices
claude-code query common-workflows
claude-code query settings
claude-code query troubleshooting
claude-code query subagents
claude-code query agent-teams
claude-code query plugins
claude-code query mcp
```

### 🤖 task - Create Coding Task

Create a coding subagent task (integrates with OpenClaw's native subagent system).

```bash
# Create a simple task
claude-code task -d "Fix login bug"

# With priority
claude-code task -d "Refactor database" -p high

# With specific model
claude-code task -d "Implement API" -m claude-3-5-sonnet
```

### ℹ️ info - Show Configuration

Display Claude Code configuration and status.

```bash
claude-code info
```

## Examples

### Example 1: Learning Best Practices

```bash
# Get started with AI coding best practices
claude-code query best-practices

# Learn about specific workflows
claude-code query "code review"
claude-code query "testing best practices"
```

### Example 2: Troubleshooting

```bash
# View troubleshooting guide
claude-code docs troubleshooting

# Query specific issue
claude-code query "authentication failed"
claude-code query "rate limit"
claude-code query "memory issues"
```

### Example 3: Development Workflow

```bash
# Start a new feature
claude-code query "feature development workflow"

# Get coding standards
claude-code query "code style"

# Learn about testing
claude-code query "testing best practices"
```

### Example 4: Advanced Features

```bash
# Learn about subagents
claude-code query subagents

# Understand agent teams
claude-code query agent-teams

# Explore MCP servers
claude-code query mcp
```

## Integration

### With OpenClaw Sessions

This skill works with OpenClaw's native capabilities:

```python
# In an OpenClaw session
from openclaw import exec

# Query documentation
result = exec("claude-code query best-practices")

# Get troubleshooting help
result = exec("claude-code query troubleshooting")
```

### With Claude Code CLI (Optional)

For users who also have Claude Code CLI installed:

```bash
# Both tools can be used together
claude-code query "API design"    # This skill
claude code "implement API"        # Claude Code CLI (if installed)
```

### Workflow Integration

**Recommended Workflow:**

1. **Start**: Query best practices
   ```bash
   claude-code query best-practices
   ```

2. **Plan**: Get workflow guidance
   ```bash
   claude-code query "feature development"
   ```

3. **Execute**: Use OpenClaw's native tools
   ```bash
   openclaw exec "git pull origin main"
   ```

4. **Review**: Get code review tips
   ```bash
   claude-code query "code review"
   ```

5. **Debug**: Troubleshooting when needed
   ```bash
   claude-code docs troubleshooting
   ```

## Documentation Links

- **Claude Code Docs**: https://code.claude.com/docs
- **OpenClaw Docs**: https://docs.openclaw.ai
- **ClawHub (Skill Registry)**: https://clawhub.com

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

### How to Contribute

1. Fork this repository
2. Create your feature branch (`git checkout -b feature/AmazingFeature`)
3. Commit your changes (`git commit -m 'Add some AmazingFeature'`)
4. Push to the branch (`git push origin feature/AmazingFeature`)
5. Open a Pull Request

### Adding New Documentation

To add new documentation topics:

1. Edit `claude-code.py`
2. Add your topic to the `DOCUMENTATION` dictionary
3. Test your changes
4. Submit a pull request

## Features

✅ **Documentation at Your Fingertips**
- Instant access to Claude Code docs
- Searchable knowledge base
- Regularly updated

✅ **Best Practices Library**
- AI coding guidelines
- Development workflows
- Security recommendations

✅ **Troubleshooting Guide**
- Common issues and solutions
- Error resolution steps
- Performance optimization tips

✅ **OpenClaw Integration**
- Seamless workflow integration
- Command-line interface
- Scriptable and automatable

✅ **Open Source**
- Transparent and auditable
- Community contributions
- Customizable

## Support

- **Issues**: Report bugs and request features
- **Discussions**: Ask questions and share workflows
- **Wiki**: Check the wiki for additional guides

## Version

- **Current Version**: 1.0.0
- **Last Updated**: 2026-02-11
- **Compatibility**: OpenClaw 2026.2.9+

## Roadmap

Future enhancements may include:

- [ ] Interactive documentation browser
- [ ] Workflow templates
- [ ] Integration with more Claude Code features
- [ ] Community-contributed guides
- [ ] Multi-language documentation

## Acknowledgments

- **Claude Code Team** for amazing AI coding tools
- **OpenClaw Team** for the awesome platform
- **Contributors** for improving this skill

---

Made with ❤️ by the OpenClaw Community

**Enjoy AI-assisted development! 🚀**
