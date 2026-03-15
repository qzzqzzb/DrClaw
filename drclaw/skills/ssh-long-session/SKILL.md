---
name: ssh-long-session
description: Manage long-running remote SSH jobs through tmux and inspect their output safely.
i18n:
  zh:
    name: SSH Long Session
    description: 通过 tmux 管理远程长任务并安全查看输出。
---

# SSH Long Session

Use this when a remote command may run longer than one response window.

## When To Use

- The remote job may take minutes or hours.
- You need to come back later and inspect the latest output.
- A plain `ssh host 'cmd'` call would likely timeout or produce too much output.

## Main Tools

1. `start_remote_tmux_session`
2. `get_remote_tmux_session_status`
3. `list_remote_tmux_sessions`
4. `terminate_remote_tmux_session`

## Recommended Flow

1. Use normal `exec` for short remote commands.
2. For a long job, call `start_remote_tmux_session` with:
   - `host`
   - `command`
   - optional `remote_cwd`
   - optional `session_name`
3. Save the returned `session_id` in your reasoning and user response when relevant.
4. Later, call `get_remote_tmux_session_status(session_id=...)` to inspect progress.
5. Prefer the returned log tail. The tool only falls back to `tmux capture-pane` if the log is unavailable.
6. When the job is no longer needed, call `terminate_remote_tmux_session`.

## Important Notes

- Prefer stable `session_name` values only when you intentionally want a recognizable remote tmux name.
- Do not start a duplicate remote session if `list_remote_tmux_sessions` already shows the same job running.
- Keep commands non-interactive. Interactive prompts will stall inside tmux.
- The remote job writes output to a remote log file so status checks stay bounded.
- Use `tail_lines` in `get_remote_tmux_session_status` to keep output concise.
