# DrClaw 用户手册

本手册主要介绍DrClaw的使用方式：

- 安装
- 配置模型
- 开始一个项目并和 agent 协作
- 出现问题的解决方案

这份手册基于当前仓库实现整理，主要参考 `install.sh`、`README.md` 和 `drclaw/cli/app.py`。

## 1. DrClaw 是什么

DrClaw 是一个面向科研工作流的多智能体框架。可以理解成一个本地运行的“科研实验室”：

- 主助手负责总控、分派任务、管理项目和智能体
- 每个项目有独立的工作区、记忆和技能配置
- 可以通过 Web、飞书、桌面端等方式接入
- 可以设置定时任务，让 agent 定期执行例行工作

如果你想运行DrClaw，最短路径是：

1. 安装 DrClaw
2. 配置 `~/.drclaw/config.json`
3. 启动 `drclaw daemon -f web`
4. 打开 `http://127.0.0.1:8080`

## 2. 安装

### 2.1 系统要求

建议环境：

- macOS 或 Linux
- `git`
- Python 3.11+

说明：安装脚本当前接受 Python 3.10+，但仓库的 [pyproject.toml](/Users/zhf27/Documents/GitHub/DrClaw/pyproject.toml) 声明为 `>=3.11`。如果你是从源码运行或准备参与开发，优先使用 Python 3.11 及以上。

### 2.2 一键安装

推荐直接使用仓库提供的安装脚本：

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/qzzqzzb/drclaw/main/install.sh)
```

安装脚本会：

- 安装 `uv`（如果本机还没有）
- 拉取仓库到 `~/.drclaw-src/`
- 安装依赖
- 把 `drclaw` 链接到 `~/.local/bin/drclaw`

安装完成后，优先检查：

```bash
drclaw status
which drclaw
```

如果提示 `drclaw: command not found`，通常是 `~/.local/bin` 还没加入 `PATH`。

### 2.3 从源码运行

如果你是在本仓库里直接开发或调试，可以用：

```bash
uv sync
```

macOS 如果要使用 tray 功能，可以安装额外依赖：

```bash
uv sync --extra tray
```

之后从仓库根目录运行：

```bash
uv run drclaw status
```

## 3. 初始化与配置

### 3.1 初始化数据目录

DrClaw 的默认数据目录是 `~/.drclaw/`。如果你不是通过安装脚本安装，先执行：

```bash
drclaw onboard
```

这个命令会初始化默认目录和配置文件。

### 3.2 配置模型提供商

DrClaw 启动前，至少要配置一个可用的 LLM provider。编辑：

```text
~/.drclaw/config.json
```

最小配置结构如下：

```json
{
  "providers": {
    "default": {
      "api_key": "YOUR_API_KEY",
      "model": "anthropic/claude-sonnet-4-5"
    }
  },
  "active_provider": "default"
}
```

如果你使用 OpenRouter，可以写成：

```json
{
  "providers": {
    "default": {
      "api_key": "sk-or-v1-...",
      "api_base": "https://openrouter.ai/api/v1",
      "model": "openrouter/anthropic/claude-sonnet-4-5"
    }
  },
  "active_provider": "default"
}
```

关于各个模型供应商的配置方式见Readme - 配置章节。

如果你希望 agent 能联网搜索网页，还可以额外配置 Serper：

```json
{
  "tools": {
    "web": {
      "serper": {
        "api_key": "YOUR_SERPER_API_KEY",
        "endpoint": "https://google.serper.dev/search",
        "max_results": 5
      }
    }
  }
}
```

### 3.3 检查配置是否生效

执行：

```bash
drclaw status
```

你应该能看到：

- 当前 DrClaw 版本
- 数据目录
- 当前激活模型
- 已注册项目数量

如果这里的模型名为空，或者不是你刚配置的值，优先检查 `config.json` 的 JSON 格式是否正确。

## 4. 推荐使用方式：通过 main agent 管理实验室

DrClaw 的推荐用法是先启动前端，然后直接和 main agent（虾秘） 对话进行管理。main agent 的功能包含：

- 创建、查看、删除项目
- 把任务分发给对应项目 agent
- 管理本地 skill hub 里的技能模板
- 把某些 skill 配置给指定项目
- 汇总各个项目 agent 的结果，再回复给你

换句话说，虾秘就是你的“实验室秘书”。但是因为DrClaw的整体设计限制，虾秘并不能为你做所有事情。例如具体项目/任务/实验的执行，仍然需要通过项目内的agent。

### 4.1 先启动一个前端

最常用的入口是 Web：

```bash
drclaw daemon -f web --debug-full
```

--debug-full 会让DrClaw记录本次运行的完整日志，有助于在出现问题时进行检查。建议开启。

然后打开：

```text
http://127.0.0.1:8080
```

如果你已经配好了飞书，也可以用：

```bash
drclaw daemon -f feishu
```

日常使用时，建议固定通过前端和 main agent 对话，不把 CLI 当成用户操作入口。

### 4.2 如何高效地和main agent 沟通

把 main agent 当成实验室总控，直接用自然语言下达管理指令即可。例如：

- “帮我创建一个项目，名字叫 ENSO-Compare，用来比较几个气候模型的结果。”
- “列出我当前所有项目，并说一下每个项目适合做什么。”
- “帮我删掉那个测试项目。”

这类请求应该先发给 main agent，而不是直接发给某个项目 agent。

### 4.3 通过 main agent 创建和管理项目

你不需要自己手动创建项目记录。更推荐这样说：

- “创建一个新项目：NSFC-2026，目标是整理基金申请书的研究基础和技术路线。”
- “给这个项目一个更明确的描述：聚焦 ENSO 预测模型的比较与复现实验。”
- “列一下现在有哪些项目，哪个更适合继续做文献综述？”

main agent 会负责：

- 创建项目记录
- 为项目准备独立工作区
- 在需要时把任务路由给对应项目 agent

建议项目名尽量明确，避免多个项目语义过近，导致main agent无法正确地进行任务分发（准确率会被指令本身和模型能力影响）。

### 4.4 通过 main agent 管理 skill

main agent 可以管理本地 skill hub，也可以把技能授予某个项目。对用户来说，更自然的方式是直接提需求：

- “看看本地 skill hub 里有哪些和论文检索相关的 skill。”
- “把适合 arXiv 检索的 skill 加到 ENSO-Compare 项目里。”
- “给 NSFC-2026 项目增加一个更适合写申请书的 skill。”
- “把这个本地 skill 目录导入 skill hub，分类放到 `proposal_writing` 下面。”

这里建议把 skill 分成两步理解：

- `local skill hub`：技能仓库，适合存放可复用模板
- `project skills`：已经授予到某个项目、可以被该项目 agent 直接使用的技能

对普通用户来说，最常见动作只有两个：

- 先让 main agent 帮你查看 skill hub 里有什么
- 再让 main agent 把合适的 skill 加到指定项目

### 4.5 通过 main agent 分发任务

当你已经有多个项目时，不要自己判断该切到哪个 agent。更推荐直接对 main agent 说：

- “把‘整理最近两周 ENSO 相关论文并给出趋势总结’分配给 ENSO-Compare 项目。”
- “把XX项目的实验结果分析一下，整理成报告发给我。”

main agent 的工作流是：

1. 先判断应该由哪个项目处理
2. 把任务路由给对应项目 agent
3. 等项目 agent 返回结果
4. 再由 main agent 汇总后回复给你

所以你面对的始终可以是 main agent，一个入口就够。

## 5. 常见工作流

### 5.1 第一次正式使用的推荐路径

如果你是第一次正式使用，建议按下面顺序：

1. 配好 `~/.drclaw/config.json`
2. 启动 `drclaw daemon -f web`
3. 在 Web 中先对 main agent 说“列出我当前有哪些项目”
4. 再说“帮我创建一个新项目，名字叫 XXX，目标是 YYY”
5. 接着说“把这个任务分配给刚才那个项目”

### 5.2 启动 Web 控制台

最常用的前端是 Web：

```bash
drclaw daemon -f web --debug-full
```

启动后打开：

```text
http://127.0.0.1:8080
```

如果你已经在 `config.json` 中配置了默认前端，也可以直接执行：

```bash
drclaw daemon
```

### 5.3 一个典型的科研项目的创建和推进

下面是一段更符合推荐用法的示例：

```text
用户：帮我创建一个项目，名字叫 ENSO-Compare，用来比较几个 ENSO 模型。

用户：看看本地 skill hub 里有没有适合论文检索和结果汇总的 skill。

用户：把合适的检索 skill 加到 ENSO-Compare 项目里。

用户：在ENSO-Compare项目下创建几个student agent，分别用于论文检索，报告生成，代码和实验 （根据你自己的需求，此处可能需要对部分agent单独配置，例如给代码agent配置所需要的实验环境，给报告生成agent配置你期望的模版格式等）

用户：让ENSO-Compare整理最近一个月 ENSO 相关论文，并按模型、数据集、评估指标分类

```

这类对话的关键点是：

- 你只和 main agent 说话
- main agent 负责选项目、分任务、汇总结果
- 你只在需要时要求更细的执行细节

### 5.4 启动飞书前端

如果你已经在 `config.json` 中配置了飞书相关参数，可以启动：

```bash
drclaw daemon -f feishu
```

相关配置位于：

```json
{
  "feishu": {
    "app_id": "",
    "app_secret": "",
    "encrypt_key": "",
    "verification_token": "",
    "allow_from": []
  }
}
```



## 6. 定时任务

DrClaw 内置 cron 服务，但推荐方式仍然是直接让 main agent 帮你安排，而不是自己手写命令。

### 6.1 通过 main agent 创建定时任务

更推荐直接这样说：

- “每天早上 8 点提醒我总结今天值得关注的研究进展。”
- “每周一上午 9 点让 ENSO-Compare 项目检查本周待办。”
- “每天晚上 10 点给我一份今天各项目的进展摘要。”
- “下周三上午 10 点提醒我检查基金申请书项目的技术路线部分。”

如果你希望 main agent 帮你补全细节，也可以直接说：

- “帮我设一个定时任务，每天早上推送论文摘要，时区用 Asia/Shanghai。”
- “给 NSFC-2026 项目加一个每周执行一次的例行检查任务。”

### 6.2 通过 main agent 查看和调整定时任务

同样建议用自然语言：

- “让ENSO-Compare项目每周五中午12点整理一下当周的进度报告”
- “每天早上8点，让资讯项目搜集并整理XXX topic的最新消息”
- “列出我当前所有定时任务。”
- “把刚才那个每天 8 点的任务改成 8 点半。”
- “先暂停 ENSO-Compare 的每周检查任务。”
- “删除刚才创建的那个测试定时任务。”
- “立刻执行一次今天早上的日报任务。”

对用户来说，你只需要表达：

- 谁来执行：main agent 还是某个项目
- 什么时候执行：每天、每周、某个具体时间
- 执行什么内容：你希望 agent 做的事情

剩下的调度细节交给 main agent 处理。

## 7. OAuth 登录

部分 provider 走 OAuth 登录流程。当前仓库提供了以下登录入口：

```bash
drclaw provider login openai-codex
drclaw provider login github-copilot
```

如果某个 provider 还没有实现对应登录处理器，当前版本会直接提示不支持。

## 8. 数据目录与文件说明

默认数据目录：

```text
~/.drclaw/
```

常见结构如下：

```text
~/.drclaw/
├── config.json
├── projects.json
├── SOUL.md
├── MEMORY.md
├── HISTORY.md
├── cron/
│   └── jobs.json
├── sessions/
├── skills/
├── local-skill-hub/
└── projects/
    └── {project_id}/
        ├── MEMORY.md
        ├── HISTORY.md
        ├── sessions/
        └── workspace/
            ├── SOUL.md
            └── skills/
```

这些文件比较重要:

- `config.json`：全局配置，尤其是模型、前端、工具配置
- `projects.json`：项目注册表
- `SOUL.md`：主助手或项目助手的人设/行为说明
- `MEMORY.md`：长期记忆
- `HISTORY.md`：历史记录
- `workspace/`：项目实际工作的文件目录

如果你要备份用户数据，优先备份整个 `~/.drclaw/`。

## 9. 重置与清理

### 9.1 仅清理记忆

保留项目、配置和技能，只清除会话与记忆：

```bash
drclaw reset --yes --memory-only
```

### 9.2 全量重置

重置本地状态，但默认保留 `config.json`：

```bash
drclaw reset --yes
```

如果你还想把配置一起恢复成默认值：

```bash
drclaw reset --yes --reset-config
```

注意：这些命令有破坏性，执行前确认你是否已经备份 `~/.drclaw/`。

## 10. 常见问题

### 10.1 `drclaw: command not found`

通常是 `~/.local/bin` 没有加入 `PATH`。把它加入 shell 配置后重新打开终端。

### 10.2 启动时报缺少依赖

如果你是从源码运行而不是走安装脚本，先执行：

```bash
uv sync
```

然后使用：

```bash
uv run drclaw ...
```

### 10.3 Web 控制台打不开

优先检查：

- 是否执行了 `drclaw daemon -f web`
- `config.json` 中的 provider 是否已配置
- 端口 `8080` 是否被占用
- 你是否在 Docker 中运行且没有设置 `daemon.web_in_docker=true`

### 10.4 main agent 说项目找不到或有歧义

通常是因为：

- 项目还没创建成功
- 两个项目名字太接近
- 你换了一个简称，但系统里记录的是另一个名字

更稳妥的说法是：

- “先列出我当前所有项目。”
- “把刚才列出的那个 `ENSO-Compare` 项目接着用起来。”
- “如果有重名，请告诉我项目 ID 和描述再让我确认。”

### 10.5 我应该用主助手还是项目助手

建议原则：

- 日常默认先找主助手
- 跨项目、管理类问题，用主助手
- 围绕单个课题长期推进时，主助手会把任务转给项目助手
- 只有在你明确知道自己要直达某个项目 agent 时，才需要绕过主助手
- 或者你的问题比较复杂，交给main agent可能会导致main agent误解你的需求，你可能需要直白地向项目agent传达你的具体需求

## 11. 建议的文档维护方式

如果你准备继续扩展这份手册，建议遵守这几个原则：

- README 保留简介和入口，详细操作放进 `docs/`
- 用户手册按任务流程组织，不按源码目录组织
- 文档优先描述用户和 main agent 的交互方式，而不是底层命令
- 当底层启动方式或管理接口变化时，同步更新本手册

