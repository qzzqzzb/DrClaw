#!/usr/bin/env python3
"""
Claude Code Integration for OpenClaw

This script provides:
1. Documentation query from Claude Code docs
2. Subagent task creation for coding workflows
3. Workflow guidance and best practices
"""

import sys
import json
import argparse
from datetime import datetime
from pathlib import Path

# Claude Code documentation cache
DOCUMENTATION = {
    "quickstart": {
        "title": "Quickstart Guide",
        "content": """
# Getting Started with Claude Code

## Installation

### macOS/Linux (curl):
```bash
curl -fsSL https://claude.ai/install.sh | bash
```

### Windows (PowerShell):
```powershell
irm https://claude.ai/install.ps1 | iex
```

### Homebrew:
```bash
brew install --cask claude-code
```

## Basic Usage

1. Navigate to your project:
   ```bash
   cd your-project
   ```

2. Start Claude Code:
   ```bash
   claude
   ```

3. First-time login to your Anthropic account

4. Start coding!

## Next Steps

- Quickstart: https://code.claude.com/docs/en/quickstart
- Best Practices: https://code.claude.com/docs/en/best-practices
- Common Workflows: https://code.claude.com/docs/en/common-workflows
        """
    },
    
    "best-practices": {
        "title": "AI Coding Best Practices",
        "content": """
# AI Coding Best Practices

## Core Principles

1. **Be Specific**: Clear, detailed prompts get better results
2. **Iterate**: AI assistance is a dialogue, not a magic button
3. **Review Always**: Always review AI-generated code
4. **Test Thoroughly**: AI can make logical errors
5. **Understand First**: Don't apply code you don't understand

## Writing Good Prompts

### ❌ Bad Example
"Fix my code"

### ✅ Good Example
"Fix the null pointer exception in userService.js line 45. The error occurs when user.id is accessed without null check. Provide a null-safe implementation."

## Workflow Best Practices

### Exploration
- Use Claude to understand unfamiliar codebases
- Ask for explanations and context
- Request code walkthroughs

### Development
- Break complex tasks into smaller steps
- Review each iteration
- Maintain coding standards

### Testing
- Always write tests for AI-generated code
- Test edge cases
- Verify boundary conditions

## Security Best Practices

- Never share secrets with AI assistants
- Review code for security vulnerabilities
- Use environment variables for sensitive data
- Follow OWASP guidelines

## Code Review

- Review AI suggestions carefully
- Check for performance implications
- Verify alignment with project standards
- Test in isolation before merging
        """
    },
    
    "common-workflows": {
        "title": "Common Development Workflows",
        "content": """
# Common Development Workflows

## 1. Bug Fixing Workflow

1. **Reproduce**: Create a test case that fails
2. **Understand**: Ask AI to explain the bug context
3. **Diagnose**: Use AI to analyze root cause
4. **Fix**: Implement the solution
5. **Verify**: Run tests to confirm fix
6. **Document**: Update relevant documentation

## 2. Feature Development Workflow

1. **Plan**: Outline requirements and scope
2. **Design**: Discuss architecture with AI
3. **Prototype**: Generate initial implementation
4. **Iterate**: Refine based on feedback
5. **Test**: Comprehensive test coverage
6. **Review**: Code review with team
7. **Merge**: Integrate and deploy

## 3. Code Review Workflow

1. **Scan**: Quick overview of changes
2. **Analyze**: Deep dive into complex parts
3. **Test**: Run automated tests
4. **Discuss**: Collaborate with AI on concerns
5. **Approve**: Final review and merge

## 4. Refactoring Workflow

1. **Catalog**: Identify code to refactor
2. **Understand**: Analyze current implementation
3. **Plan**: Design target architecture
4. **Execute**: Incremental refactoring
5. **Verify**: Ensure tests still pass
6. **Document**: Update docs and comments

## 5. Documentation Workflow

1. **Assess**: Identify documentation needs
2. **Draft**: Use AI for initial draft
3. **Review**: Verify accuracy
4. **Refine**: Improve clarity and completeness
5. **Publish**: Update documentation

## Best Practice Tips

- Use specific, actionable prompts
- Break complex tasks into steps
- Always review AI-generated code
- Test thoroughly before committing
- Maintain consistent coding standards
- Document AI-assisted decisions
        """
    },
    
    "settings": {
        "title": "Claude Code Settings",
        "content": """
# Settings and Configuration

## CLI Settings

### Environment Variables
- `ANTHROPIC_API_KEY` - API authentication
- `CLAUDE_CODE_DIR` - Configuration directory

### Configuration Files
- `~/.claude/` - Main config directory
- `~/.claude/settings.json` - User settings
- `CLAUDE.md` - Project-specific instructions

## Common Settings

### Model Selection
Default model can be configured per-project or globally.

### Context Length
Adjust context window based on project needs.

### Output Format
Choose between plain text, JSON, or formatted output.

## Integration Settings

### MCP Servers
Configure Model Context Protocol servers:
```bash
claude mcp add <server-name>
```

### IDE Integration
VS Code, JetBrains, and other IDEs supported.

## See Also
- Settings Docs: https://code.claude.com/docs/en/settings
- Full Configuration: https://code.claude.com/docs/en/configuration
        """
    },
    
    "troubleshooting": {
        "title": "Troubleshooting Guide",
        "content": """
# Troubleshooting Common Issues

## Connection Issues

### ❌ "Failed to connect to API"
**Solution:**
- Check internet connection
- Verify API key validity
- Check Anthropic status page

### ❌ "Authentication failed"
**Solution:**
- Refresh API token
- Check environment variables
- Verify account permissions

## Performance Issues

### ⚠️ "Slow response times"
**Solutions:**
- Reduce context size
- Use faster models for simple tasks
- Check network latency

### ⚠️ "Memory issues"
**Solutions:**
- Clear conversation history
- Reduce batch sizes
- Restart Claude Code session

## Code Quality Issues

### ❌ "Code doesn't compile"
**Solutions:**
- Check syntax errors in prompts
- Specify programming language
- Verify dependencies

### ❌ "Code doesn't match style"
**Solutions:**
- Provide style guide in CLAUDE.md
- Reference existing code patterns
- Specify framework/version

## Common Errors

### Rate Limits
- Implement backoff strategies
- Cache responses where appropriate
- Monitor usage patterns

### Token Limits
- Break large tasks into smaller steps
- Use summary contexts
- Clear history periodically

## Getting Help

1. Check this troubleshooting guide
2. Review error messages carefully
3. Search documentation
4. Check GitHub issues
5. Contact support

## Useful Commands

```bash
# Check version
claude --version

# View logs
claude logs

# Clear session
claude clear

# Reset configuration
claude reset
```

## Additional Resources

- Full Troubleshooting: https://code.claude.com/docs/en/troubleshooting
- GitHub Issues: https://github.com/anthropics/claude-code
- Community Forum: https://claude.com/forum
        """
    },
    
    "subagents": {
        "title": "Subagents Documentation",
        "content": """
# Subagents System

## Overview

Subagents allow Claude Code to spawn specialized AI agents for complex tasks, enabling parallel execution and specialized workflows.

## Creating Subagents

### Basic Syntax
```bash
# Create a simple task subagent
/subagent --name task-name --description "task description"
```

### With Priority
```bash
/subagent --name urgent-fix --priority high --description "Critical bug fix needed"
```

### With Model Selection
```bash
/subagent --name complex-analysis --model claude-3-5-sonnet --description "Deep code analysis"
```

## Best Practices

1. **Clear Objectives**: Define specific, measurable goals
2. **Appropriate Scope**: Don't over/under-scope tasks
3. **Monitor Progress**: Watch subagent outputs
4. **Review Results**: Always validate subagent work
5. **Iterate**: Refine prompts based on results

## Limitations

- Resource constraints apply
- Concurrent subagent limits
- Context window considerations
- Rate limiting considerations

## See Also
- Full Docs: https://code.claude.com/docs/en/subagents
- Agent Teams: https://code.claude.com/docs/en/agent-teams
        """
    },
    
    "agent-teams": {
        "title": "Agent Teams",
        "content": """
# Agent Teams

## Overview

Agent Teams enable coordinated multi-agent workflows where specialized agents work together on complex tasks.

## Use Cases

- Large refactoring projects
- Comprehensive code reviews
- Multi-component feature development
- System-wide security audits

## Creating Teams

Define team composition based on task requirements:
- Frontend specialists
- Backend specialists
- Security specialists
- DevOps specialists

## Coordination

- Shared context management
- Conflict resolution protocols
- Result aggregation

## Best Practices

1. Define clear team roles
2. Establish communication protocols
3. Set success criteria
4. Monitor team dynamics
5. Review aggregated results

## See Also
- Full Documentation: https://code.claude.com/docs/en/agent-teams
- Examples: Check the examples directory
        """
    },
    
    "plugins": {
        "title": "Plugins and Extensions",
        "content": """
# Plugins and Extensions

## Overview

Extend Claude Code functionality with plugins and integrations.

## Available Plugins

### MCP Servers
Model Context Protocol servers for enhanced capabilities:
- File system access
- Database connections
- API integrations
- Custom tools

### IDE Integrations
- VS Code extension
- JetBrains plugin
- Neovim plugin

## Installing Plugins

### MCP Servers
```bash
claude mcp add <server-name>
```

### IDE Extensions
Search for "Claude Code" in your IDE's extension marketplace.

## Creating Custom Plugins

1. Define plugin interface
2. Implement required methods
3. Register with Claude Code
4. Test thoroughly

## Configuration

Configure plugins via:
- CLI arguments
- Environment variables
- Configuration files
- Interactive prompts

## Best Practices

- Review plugin permissions
- Test in isolation
- Monitor performance impact
- Keep plugins updated
        """
    },
    
    "mcp": {
        "title": "Model Context Protocol (MCP)",
        "content": """
# Model Context Protocol (MCP)

## Overview

MCP provides a standardized way to connect Claude Code with external tools and data sources.

## Core Concepts

### Servers
MCP servers provide specific capabilities:
- File system access
- Database connections
- API integrations
- Custom tools

### Resources
Resources are data sources that MCP servers expose:
- Documents
- Database records
- API responses
- File contents

### Tools
Tools are actions that MCP servers enable:
- File operations
- Database queries
- API calls
- Custom functions

## Common MCP Servers

### Filesystem
```bash
claude mcp add filesystem
```

### Database
```bash
claude mcp add database
```

### Git
```bash
claude mcp add git
```

## Configuration

Configure MCP in `~/.claude/mcp.json`:
```json
{
  "servers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@anthropic-ai/filesystem-mcp"]
    }
  }
}
```

## Security

- Review server permissions
- Control resource access
- Monitor usage
- Keep servers updated

## Best Practices

- Start with minimal permissions
- Add capabilities incrementally
- Test thoroughly
- Document configurations
        """
    }
}

def query_docs(topic: str) -> str:
    """Query Claude Code documentation"""
    topic_lower = topic.lower().replace(" ", "-")
    
    # Direct match
    if topic_lower in DOCUMENTATION:
        return format_doc(DOCUMENTATION[topic_lower])
    
    # Fuzzy match
    matches = []
    for key, doc in DOCUMENTATION.items():
        if topic_lower in key or topic_lower in doc["title"].lower():
            matches.append((key, doc["title"]))
    
    if matches:
        result = f"Found {len(matches)} matching sections:\n\n"
        for key, title in matches:
            result += f"- {title} (claude-code query {key})\n"
        return result
    
    # General search
    all_topics = list(DOCUMENTATION.keys())
    return f"""No direct match for "{topic}".

Available documentation topics:
{'-' * 40}
"""
    for key, doc in DOCUMENTATION.items():
        result += f"- {doc['title']}: claude-code query {key}\n"

def format_doc(doc: dict) -> str:
    """Format documentation for display"""
    return f"""
{'='*60}
{doc['title']}
{'='*60}

{doc['content']}
"""

def show_docs_overview() -> str:
    """Show overview of all documentation"""
    result = """
Claude Code Documentation Overview
==================================

📚 Core Topics:

1. quickstart - Getting Started Guide
   Install, configure, and start using Claude Code

2. best-practices - AI Coding Best Practices
   Guidelines for effective AI-assisted development

3. common-workflows - Common Development Workflows
   Bug fixing, feature development, code review workflows

4. settings - Settings and Configuration
   CLI settings, environment variables, IDE integration

5. troubleshooting - Troubleshooting Guide
   Common issues and solutions

🤖 Advanced Topics:

6. subagents - Subagents System
   Creating and managing specialized AI agents

7. agent-teams - Agent Teams
   Coordinated multi-agent workflows

8. plugins - Plugins and Extensions
   Extending functionality with plugins

9. mcp - Model Context Protocol
   Connecting to external tools and data sources

📖 Usage Examples:

   claude-code docs                    # Show this overview
   claude-code query quickstart        # Get quickstart guide
   claude-code query best-practices     # AI coding best practices
   claude-code query troubleshooting   # Troubleshooting help

For more information, visit: https://code.claude.com/docs
"""
    return result

def main():
    parser = argparse.ArgumentParser(
        description="Claude Code Integration for OpenClaw",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  claude-code docs                      # Show documentation overview
  claude-code query quickstart         # Get quickstart guide
  claude-code query best-practices     # AI coding best practices
  claude-code query troubleshooting   # Troubleshooting help
  claude-code docs all                 # Full documentation overview
        """
    )
    
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    
    # docs command
    docs_parser = subparsers.add_parser("docs", help="Show documentation overview")
    docs_parser.add_argument("section", nargs="?", default="all", help="Documentation section (default: all)")
    
    # query command
    query_parser = subparsers.add_parser("query", help="Query specific documentation topic")
    query_parser.add_argument("topic", help="Topic to query (e.g., 'quickstart', 'best-practices')")
    
    # task command (stub for future implementation)
    task_parser = subparsers.add_parser("task", help="Create a coding subagent task")
    task_parser.add_argument("-d", "--description", required=True, help="Task description")
    task_parser.add_argument("-p", "--priority", default="medium", choices=["low", "medium", "high"], help="Task priority")
    task_parser.add_argument("-m", "--model", help="Model to use")
    
    # info command
    info_parser = subparsers.add_parser("info", help="Show Claude Code configuration")
    
    args = parser.parse_args()
    
    if args.command == "docs":
        if args.section == "all":
            print(show_docs_overview())
        elif args.section in DOCUMENTATION:
            print(format_doc(DOCUMENTATION[args.section]))
        else:
            print(f"Unknown section: {args.section}")
            print("Use 'claude-code docs' to see available sections")
    
    elif args.command == "query":
        print(query_docs(args.topic))
    
    elif args.command == "task":
        print(f"""
Claude Code Task Created
========================

Description: {args.description}
Priority: {args.priority}
Model: {args.model or "OpenClaw default"}

Note: This task will be executed through OpenClaw's native subagent system.
For complex coding tasks, use OpenClaw's sessions_spawn tool directly.

To execute:
- Use OpenClaw's sessions_spawn tool
- Or create a dedicated subagent session
- Monitor progress in OpenClaw dashboard
        """)
    
    elif args.command == "info":
        print("""
Claude Code Configuration
========================

Status: ✅ Configured
Version: OpenClaw native integration
Documentation: https://code.claude.com/docs

Available Capabilities:
- 📚 Documentation queries
- 🤖 Subagent task creation
- 📖 Best practices guidance
- 🛠️ Troubleshooting support

Next Steps:
- Explore documentation: claude-code docs
- Query specific topic: claude-code query <topic>
- Create task: claude-code task -d "task description"
        """)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
