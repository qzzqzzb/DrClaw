---
name: claude-code
description: 'Claude Code integration for OpenClaw. This skill provides interfaces
  to:

  - Query Claude Code documentation from https://code.claude.com/docs

  - Manage subagents and coding tasks

  - Execute AI-assisted coding workflows

  - Access best practices and common workflows

  Use this skill when users want to:

  - Get help with coding tasks

  - Query Claude Code documentation

  - Manage AI-assisted development workflows

  - Execute complex programming tasks

  '
homepage: https://code.claude.com/docs
i18n:
  zh:
    description: Claude Code 集成。
---

# Claude Code Integration

This skill integrates Claude Code capabilities into OpenClaw, providing access to AI-assisted coding workflows, documentation, and best practices.

## What You Can Do

### 📚 Documentation Queries
- Query Claude Code documentation
- Get best practices and workflows
- Learn about settings and customization
- Troubleshoot common issues

### 🤖 Subagent Management
- Create coding subagents
- Manage agent teams
- Execute complex development tasks
- Automate code reviews and PR workflows

### 🛠️ Development Workflows
- Best practices for AI-assisted coding
- Common workflows and patterns
- Settings and configuration
- Troubleshooting guidance

## Usage Examples

### Query Documentation
```bash
# Get documentation about a specific topic
claude-code query "subagents"
claude-code query "best practices"
claude-code query "settings"
```

### Execute Coding Task
```bash
# Create a coding subagent for a complex task
claude-code task --description "Fix the login bug" --priority high
claude-code task --description "Refactor the database layer" --model claude-3-5-sonnet
```

### List Available Commands
```bash
# Show all available commands
claude-code --help
```

## Available Commands

### query
Query Claude Code documentation for a specific topic.

**Usage:**
```bash
claude-code query <topic>
```

**Examples:**
```bash
claude-code query "subagents"
claude-code query "agent-teams"
claude-code query "best practices"
claude-code query "common workflows"
claude-code query "settings"
claude-code query "troubleshooting"
```

**Topics include:**
- Subagents and agent teams
- Best practices and workflows
- Settings and customization
- Troubleshooting guide
- Plugins and extensions
- MCP (Model Context Protocol)
- Headless/Programmatic usage

### task
Create and execute a coding subagent task.

**Usage:**
```bash
claude-code task --description "<task description>" [--priority <level>] [--model <model-name>]
```

**Options:**
- `--description, -d`: Task description (required)
- `--priority, -p`: Task priority (low/medium/high, default: medium)
- `--model, -m`: Model to use (optional, uses default if not specified)

**Examples:**
```bash
claude-code task --description "Implement user authentication module"
claude-code task --description "Refactor database queries" --priority high
claude-code task --description "Write unit tests for the API" --model claude-3-5-sonnet
```

### docs
Get overview of Claude Code documentation sections.

**Usage:**
```bash
claude-code docs [section]
```

**Sections:**
- `quickstart` - Getting started guide
- `best-practices` - AI coding best practices
- `common-workflows` - Typical development workflows
- `settings` - Customization options
- `troubleshooting` - Common issues and solutions
- `all` - Full documentation overview (default)

**Examples:**
```bash
claude-code docs
claude-code docs quickstart
claude-code docs best-practices
claude-code docs troubleshooting
```

### info
Display Claude Code configuration and status.

**Usage:**
```bash
claude-code info
```

**Output includes:**
- Version information
- Available subagents
- Configured models
- MCP servers status

## Integration with OpenClaw

This skill works seamlessly with OpenClaw's native capabilities:

- **Subagents**: Claude Code subagents complement OpenClaw's subagent system
- **Code Execution**: Use with OpenClaw's exec tool for complete development workflow
- **File Management**: Combine with OpenClaw's read/write tools for full codebase management
- **Sessions**: Claude Code tasks integrate with OpenClaw's session management

## Example Workflows

### Complex Bug Fix
```bash
# 1. Query best practices for debugging
claude-code query "debugging best practices"

# 2. Create a subagent to investigate and fix
claude-code task --description "Find and fix the null pointer exception in userService.js" --priority high

# 3. Review the changes
claude-code query "code review best practices"
```

### New Feature Development
```bash
# 1. Get best practices for the feature type
claude-code query "API design best practices"

# 2. Create development task
claude-code task --description "Implement REST API for user management" --priority medium

# 3. Check settings for code style
claude-code query "code style settings"
```

### Code Review Automation
```bash
# 1. Query PR review best practices
claude-code query "PR review workflows"

# 2. Set up automated review task
claude-code task --description "Review all PRs in the last week" --priority low
```

## Configuration

### Environment Variables
Not required for basic usage. Claude Code integration uses OpenClaw's native capabilities.

### Models
Uses OpenClaw's configured default models. Override per task with `--model` option.

### Subagent Limits
Managed by OpenClaw's subagent configuration (default: 8 concurrent subagents).

## Notes

- This skill provides a wrapper around Claude Code documentation and workflows
- Complex coding tasks are executed through OpenClaw's native subagent system
- For direct Claude Code CLI usage, install Claude Code separately from https://claude.com/code
- All task execution happens through OpenClaw's secure agent infrastructure

## See Also

- Claude Code Official Docs: https://code.claude.com/docs
- OpenClaw Subagents: Use OpenClaw's native subagent functionality
- Best Practices: Integrated from Claude Code guidelines
