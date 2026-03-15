# DeepSlides - Group Meeting Slides Generator

## 技能元数据

| 属性 | 值 |
|------|-----|
| **名称** | group-meeting-slides |
| **版本** | 1.0.0 |
| **触发条件** | 用户请求生成学术演示文稿/组会报告 PPT |
| **描述** | 基于 AI 研究的组会演示文稿生成工具，自动完成从主题到 PPTX 文件的完整流程 |

---

## 概述

DeepSlides 是一个轻量级的幻灯片生成工作流。它能自动从用户给定的主题生成专业的学术演示文稿。

### 核心能力

1. **智能研究** - 自动网络搜索，收集相关资料
2. **内容生成** - AI 撰写结构化报告内容
3. **视觉设计** - AI 设计幻灯片布局和样式
4. **代码渲染** - 生成 python-pptx 代码并渲染为 PPTX 文件

---

## 触发条件

当用户表达以下意图时触发此技能：

- 生成组会汇报 PPT
- 制作学术演示文稿
- 从主题创建幻灯片
- 将报告转换为演示文稿

### 触发示例

```
"帮我做一个关于 XXX 的组会报告 PPT"
"生成一个 10 分钟的学术演示"
"我要做组会汇报，主题是 XXX"
"Create a presentation for our group meeting"
```

---

## 输入规范

### 必需输入

| 字段 | 类型 | 说明 |
|------|------|------|
| `topic` | string | 演示主题 |

### 可选输入

| 字段 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `presentation_minutes` | string | "10" | 演示时长（分钟） |
| `style` | string | null | 期望的演示风格 |
| `image_path` | string | null | 输入图像路径（可选，用于从图片提取主题） |

### 风格选项

- `professional business` - 商务专业
- `modern tech` - 现代科技
- `minimalist` - 极简主义
- `creative lively` - 创意活泼
- `academically rigorous` - 学术严谨
- `storytelling narrative` - 故事叙述
- `magazine visual` - 杂志视觉
- `illustration cartoon` - 卡通插画
- `retro nostalgic` - 复古怀旧
- `data visualization` - 数据可视化

### 故事线模板

- **Problem-Solution** - 问题-解决方案
- **Situation-Conflict-Resolution-Outcome** - 场景-冲突-解决-结果
- **SCQA** - 场景-冲突-问题-答案
- **Timeline** - 时间线（过去-现在-未来）
- **Contrast** - 对比（现在 vs 未来）
- **Pyramid** - 金字塔结构
- **Research Report** - 研究报告

---

## 输出规范

### 输出文件

在 `saves_sonnet/{topic}/` 目录下生成：

```
saves_sonnet/{topic}/
├── cover_slide.pptx           # 封面幻灯片
├── section_slide_1.pptx      # 章节分隔页
├── section_slide_2.pptx
├── {section}_slide_1.pptx    # 内容幻灯片
├── {section}_slide_2.pptx
├── ...
├── end_slide.pptx             # 结尾页
└── {topic}_final.pptx        # 最终合并的 PPTX
```

### 输出格式

- **PPTX** - PowerPoint 原生格式 (.pptx)
- **宽屏比例** - 16:9 (13.33 × 7.5 inches)

---

## 工作流程

### 完整流程图

```
┌──────────────────────────────────────────────────────────────────────────────────────┐
│                              DeepSlides Pipeline                                    │
└──────────────────────────────────────────────────────────────────────────────────────┘

  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐
  │  INPUT  │───▶│ PLANNING│───▶│RESEARCH │───▶│  WRITE  │───▶│  PPT    │
  │         │    │         │    │         │    │         │    │ PLANNING│
  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └────┬────┘
                                                                       │
                                                                       ▼
  ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌─────────┐    ┌────────┴────────┐
  │ OUTPUT  │◀───│ COMPILE │◀───│  END   │◀───│SECTION  │◀───│  PPT STYLES    │
  │  PPTX   │    │  PPT    │    │ SLIDE  │    │ SLIDES  │    │                 │
  └─────────┘    └─────────┘    └─────────┘    └─────────┘    └─────────────────┘
```

### 阶段 1: 输入处理

**节点**: `process_image_input`

- 接受文本主题或图像输入
- 使用 Vision API 提取图像描述
- 推断用户意图

### 阶段 2: 报告规划

**节点**: `generate_report_plan`

1. 生成初始搜索查询 (Planner)
2. 执行初始搜索收集背景信息
3. 生成报告大纲

**节点**: `human_feedback`

- 用户审阅和反馈报告结构
- 支持迭代修改

### 阶段 3: 研究与写作

**子图**: `section_builder` (并行执行每个章节)

1. `generate_queries` - 生成章节搜索查询
2. `search_web` - 执行网络搜索 (Tavily)
3. `write_section` - 撰写章节内容

### 阶段 4: 最终章节

1. `gather_completed_sections` - 收集完成的章节
2. `write_final_sections` - 撰写引言和结论
3. `compile_final_report` - 编译完整报告

### 阶段 5: PPT 规划

**节点**: `generate_ppt_outline`

1. 确定故事线 (Storyline)
2. 推荐幻灯片数量 (PPT Length)
3. 分配章节幻灯片数 (Section Distribution)
4. 生成 PPT 大纲 (PPT Outline)

### 阶段 6: PPT 样式

**节点**: `generate_ppt_styles`

- 生成整体视觉风格
- 确定颜色方案 (主色、强调色)
- 选择字体
- 定义背景色调

### 阶段 7: PPT 幻灯片生成

**子图**: `ppt_section_graph`

#### 7.1 封面和章节页

- `generate_cover_slide` - 封面
- `generate_section_cover_slides` - 章节分隔页
- `generate_end_slide` - 结尾页

#### 7.2 内容幻灯片

**子图**: `ppt_slide_graph`

对每张幻灯片：

1. `enrich_slide_content`
   - 生成搜索查询
   - 搜索相关内容
   - 丰富要点内容
   - 生成布局描述

2. `generate_slide_code_and_execute`
   - 生成 python-pptx 代码
   - 执行代码生成 PPTX

3. `ppt_slide_to_image_and_validate`
   - 转换为图片（可选）
   - 验证质量

### 阶段 8: PPT 编译

**节点**: `compile_ppt`

- 合并所有单页 PPTX
- 使用 XML 深度合并，保留格式

---

## 智能体

DeepSlides 使用 **4 个智能体** 协同工作：

| 阶段 | 智能体 | 功能 |
|------|--------|------|
| 研究阶段 | Writer | 生成搜索查询和报告大纲 |
| 写作阶段 | Writer | 撰写报告章节内容 |
| PPT 规划 | Planner | 生成 PPT 大纲和故事线 |
| PPT 设计 | Designer | 生成风格和颜色方案、布局描述 |
| 代码生成 | Coder | 生成 python-pptx 代码 |

---

## 技术实现

### 安装依赖

本技能依赖以下工具：

#### 1. Python 依赖

```bash
# 安装pptx-tool依赖
cd ./pptx-tools
pip install -e . --break-system-packages

# 手动安装核心依赖
pip install python-pptx lxml
```

#### 2. 系统工具

| 工具 | 用途 | 安装方式 |
|------|------|----------|
| `soffice` (LibreOffice) | 将 PPTX 导出为 PNG 图片进行视觉理解 | 详见下方 |

**LibreOffice 安装：**

```bash
# Ubuntu/Debian
sudo apt install libreoffice

# macOS
brew install --cask libreoffice

# Windows
# 下载安装: https://www.libreoffice.org/download/download/
```

### 依赖库

| 库 | 用途 |
|-----|------|
| `python-pptx` | PPTX 文件操作 |
| `lxml` | XML 合并 |
| `pptx-tools` | 本地扩展（渐变、图片填充等） |

### python-pptx 代码规范

```python
# 画布尺寸
宽度: 13.33 inches
高度: 7.5 inches
比例: 16:9

# 字体规范
一级标题: 36pt
二级标题: 20pt
正文: 20pt
中文字体: 微软雅黑
英文字体: Arial
```

### 工具函数 (pptx_tools)

- `add_gradient_shape` - 渐变填充形状
- `add_gradient_background` - 渐变背景
- `add_solid_shape` - 纯色形状
- `add_image_filled_shape` - 图片填充形状
- `add_textbox` - 自动调整大小的文本框
- `add_line` - 绘制线条

---

## 文件结构 (按阶段组织)

```
group-meeting-skill/
├── skill.md                    # 本文件 - 技能主文档
├── workflow.md                 # 完整工作流程文档
│
├── prompts/                    # 按阶段分类的 Prompts
│   ├── prompts_research.md    # 研究阶段
│   ├── prompts_writing.md     # 写作阶段
│   ├── prompts_ppt_planning.md # PPT 规划阶段
│   ├── prompts_ppt_design.md  # PPT 设计阶段
│   └── prompts_ppt_code.md    # PPT 代码阶段
│
└── persona/                    # 智能体人设
    ├── persona_planner.md    # Planner
    ├── persona_writer.md     # Writer
    ├── persona_designer.md   # Designer
    └── persona_coder.md      # Coder
```

### 与代码节点对应关系

| 阶段 | 代码节点 | Prompt 文件 |
|------|----------|-------------|
| 研究 | `generate_queries`, `search_web` | prompts_research.md |
| 写作 | `write_section`, `write_final_sections` | prompts_writing.md |
| PPT 规划 | `generate_ppt_outline` | prompts_ppt_planning.md |
| PPT 设计 | `generate_ppt_styles`, `enrich_slide_content` | prompts_ppt_design.md |
| PPT 代码 | `generate_slide_code_and_execute` | prompts_ppt_code.md |

---

## 使用示例

### 示例 1: 基本用法

```
用户: 帮我做一个关于机器学习在医疗领域应用的组会报告
技能: 生成 10 分钟演示，包含完整研究和 PPT
```

### 示例 2: 指定时长

```
用户: 我需要一个 15 分钟的量子计算组会报告
技能: 调整内容深度，生成约 15 张幻灯片
```

### 示例 3: 指定风格

```
用户: 用学术严谨的风格做一个气候变化的主题报告
技能: 应用学术样式，包含图表和数据可视化
```

### 示例 4: 图像输入

```
用户: [分享一张论文截图]
技能: 使用视觉模型提取主题和意图
```

---

## 质量控制

- **人工反馈** - 审阅和修改报告大纲
- **内容评分** - AI 评估内容完整性，使用图片理解技能，视觉检查
- **设计评分** - AI 评估布局和美学，使用图片理解技能，视觉检查
- **引用追踪** - 正确标注信息来源
