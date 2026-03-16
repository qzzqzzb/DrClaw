# DeepSlides 工作流程

本文档详细描述了 DeepSlides 从输入到输出 PPTX 文件的完整工作流程。

---

## 阶段详细说明

### 阶段 1: 输入处理

**节点**: `process_image_input`

**功能**:
- 如果用户提供了图像，使用图像理解功能提取图像描述
- 从图像中推断用户意图
- 提取或确定报告主题

**输入**:
- `topic`: 文本主题（可选）
- `image_path`: 图像路径（可选）

**输出**:
- `topic`: 确定的主题
- `caption`: 图像描述
- `user_intent`: 用户意图

---

### 阶段 2: 报告规划

**节点**: `generate_report_plan`

**功能**:
1. 生成初始搜索查询（使用 Planner 模型）
2. 执行初始搜索收集背景信息
3. 基于搜索结果生成报告大纲

**使用的 Prompt**:
- `report_planner_query_writer_instructions` - 生成初始查询
- `report_planner_instructions` - 生成报告大纲

**输出**:
- `sections`: 章节列表（包含 name, description, research 标志）

**人工反馈**:
- `human_feedback` 节点允许用户审阅和反馈报告结构
- 支持迭代修改直到用户满意

---

### 阶段 3: 研究与写作

**子图**: `section_builder`（并行执行每个章节）

对每个需要研究的章节，依次执行：

#### 3.1 `generate_queries`

**功能**: 为当前章节生成搜索查询

**使用的 Prompt**:
- `query_writer_instructions` - 章节级查询生成

**输出**:
- `search_queries`: 搜索查询列表

#### 3.2 `search_web`

**功能**: 执行网络搜索，获取相关信息

**工具**:
- `enhanced_tavily_search` - Tavily 搜索 API

**输出**:
- `source_str`: 格式化的搜索结果字符串

#### 3.3 `write_section`

**功能**: 基于搜索结果撰写章节内容

**使用的 Prompt**:
- `section_writer_instructions` - 章节撰写

**输出**:
- `report_sections_from_research`: 章节内容

---

### 阶段 4: 最终章节

**节点**:

1. **`gather_completed_sections`** - 收集所有完成的章节
2. **`write_final_sections`** - 撰写引言和结论
3. **`compile_final_report`** - 编译完整报告

**使用的 Prompt**:
- `final_section_writer_instructions` - 最终章节撰写

**输出**:
- `final_report`: 完整的 Markdown 报告

---

### 阶段 5: PPT 规划 ⭐

**节点**: `generate_ppt_outline`

**功能**: 生成 PPT 大纲

**步骤**:

1. **故事线确定** (`storyline_prompt`)
   - 确定演示风格
   - 选择故事线模板（Problem-Solution, SCQA, Timeline 等）
   - 推荐主题色

2. **长度规划** (`ppt_length_prompt`)
   - 根据演示时长推荐幻灯片数量

3. **章节分配** (`ppt_section_distribution_prompt`)
   - 重新规划章节结构
   - 分配每章节幻灯片数

4. **大纲生成** (`ppt_outline_prompt`)
   - 生成每张幻灯片的标题和要点
   - 确保要点数量有变化（3-6个）

**输出**:
- `ppt_outline`: PPTOutline 对象
- `section_distribution`: 章节分配字典

---

### 阶段 6: PPT 样式

**节点**: `generate_ppt_styles`

**功能**: 生成整体视觉风格

**使用的 Prompt**:
- `style_prompt` / `style_plan_prompt` - 风格生成
- `color_examples_prompt` - 颜色示例参考

**输出**:
- `style`: 风格类型
- `main_color`: 主色
- `accent_color`: 强调色
- `background_tone`: 背景色调
- `heading_font_color`: 标题字体颜色
- `body_font_color`: 正文字体颜色
- `font_name`: 字体名称
- `style_summary`: 风格描述

---

### 阶段 7: PPT 幻灯片生成

**子图**: `ppt_section_graph`

对每个 PPT 章节执行：

#### 7.1 章节封面

**节点**: `generate_ppt_section_start` / `generate_ppt_section_end`

**功能**: 生成章节分隔幻灯片

#### 7.2 内容幻灯片

**子图**: `ppt_slide_graph`

对每张幻灯片执行：

##### 7.2.1 `enrich_slide_content`

**功能**:
1. 为当前幻灯片生成搜索查询
2. 搜索相关内容
3. 丰富幻灯片要点内容
4. 生成幻灯片布局描述

**使用的 Prompt**:
- `query_writer4PPT_instructions` - PPT 查询生成
- `content_enrichment_prompt` - 内容丰富化
- `detail_prompt` / `design_prompt` - 布局描述

**输出**:
- `enriched_points`: 丰富后的要点
- `slide_detail`: 布局描述 JSON
- `image_data`: 嵌入的图片数据

##### 7.2.2 `generate_slide_code_and_execute`

**功能**:
1. 生成 python-pptx 代码
2. 执行代码生成 PPTX 文件

**使用的 Prompt**:
- `code_prompt` - PPT 代码生成
- `ppt_tools_prompt` - PPT 工具函数

**代码规范**:
```python
# 画布尺寸: 16:9 (13.33 × 7.5 inches)

# 字体规范
Level-1 标题: 36pt
其他标题: 20pt
正文: 20pt
中文字体: 微软雅黑
英文字体: Arial
```

**输出**:
- 单页 PPTX 文件: `{section_name}_slide_{index}.pptx`

##### 7.2.3 `ppt_slide_to_image_and_validate`

**功能**:
1. 将 PPTX 转换为图片（可选）
2. 验证幻灯片质量

---

### 阶段 8: PPT 编译

**节点**: `generate_cover_slide`, `generate_section_cover_slides`, `generate_end_slide`, `compile_ppt`

**功能**:

1. **生成封面** - 标题页
2. **生成章节分隔页** - 每个章节的起始页
3. **生成结尾页** - 致谢/参考页
4. **合并 PPTX** - 将所有单页合并为最终文件

**合并原理**:
- 使用 Python `zipfile` 和 `lxml` 库
- 深度合并 XML，保留所有格式和复杂元素
- 合并 content types, relationships, slides 等

**最终输出**:
- `{topic}_final.pptx`

---

## python-pptx 代码规范

### 画布规格

```
宽度: 13.33 inches (960px)
高度: 7.5 inches (540px)
比例: 16:9
```

### 字体规范

| 元素 | 字体 | 大小 |
|-----|------|------|
| 一级标题 | 微软雅黑 / Arial | 36pt |
| 二级标题 | 微软雅黑 / Arial | 20pt |
| 正文 | 微软雅黑 / Arial | 20pt |

### 工具函数 (pptx_tools)

项目扩展了 python-pptx，提供以下工具函数：

| 函数 | 说明 |
|------|------|
| `add_gradient_shape` | 添加渐变填充形状 |
| `add_gradient_background` | 添加渐变背景 |
| `add_solid_shape` | 添加纯色形状 |
| `add_image_filled_shape` | 添加图片填充形状 |
| `add_textbox` | 添加自动调整大小的文本框 |
| `add_line` | 绘制线条 |

### 代码示例

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx_tools import add_gradient_shape, add_textbox

# 创建演示文稿
prs = Presentation()
prs.slide_width = Inches(13.33)
prs.slide_height = Inches(7.5)

# 添加幻灯片
slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank layout

# 添加标题
title = add_textbox(slide, x=0.5, y=0.3, w=12, h=0.8)
title.text = "Slide Title"
title.text_frame.paragraphs[0].font.size = Pt(36)

# 添加内容
content = add_textbox(slide, x=0.5, y=1.3, w=12, h=5)
content.text = "Bullet point 1\nBullet point 2"

# 保存
prs.save('slide.pptx')
```

---

## 使用的模型

| 阶段 | 模型 | 说明 |
|------|------|------|
| 报告规划 | Planner | 生成搜索查询和报告大纲 |
| 内容撰写 | Writer | 撰写报告章节内容 |
| PPT 设计 | Designer | 生成幻灯片布局和设计 |
| 代码生成 | Coder | 生成 python-pptx 代码 |

---

## 输出文件

流程完成后，在 `saves_sonnet/{topic}/` 目录下生成：

```
saves_sonnet/{topic}/
├── cover_slide.pptx           # 封面
├── section_slide_1.pptx      # 章节分隔页
├── section_slide_2.pptx
├── ...
├── {section}_slide_1.pptx    # 内容幻灯片
├── {section}_slide_2.pptx
├── ...
├── end_slide.pptx             # 结尾页
└── {topic}_final.pptx        # 最终合并的 PPTX
```