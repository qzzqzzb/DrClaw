# PPT Code Prompts - PPT 代码阶段

本文档包含 **PPT 代码阶段** 的所有 prompts，对应工作流中的 `generate_slide_code_and_execute` 节点。

---

## 1. PPT Tools

**节点**: `generate_slide_code_and_execute`

**智能体**: Coder

**用途**: 提供 PPT 创建的实用工具函数

**Prompt**:

```
First, complete the background layer, then complete the layout layer, and finally complete the content layer. Use the add_textbox function to place all the text. Do not use slide.shapes.add_textbox.

These are utility functions you can choose to use to reduce repetitive work in your PowerPoint slides. You don't need to use all the functions, just select the appropriate ones to complete your task. Please strictly follow the designed layout when coding; do not change the design to accommodate the utility functions.

**You must use the "add_textbox" function provided in the given tools below, DO NOT use "slide.shapes.add_textbox".**

Available functions include:
- add_gradient_shape: Add a shape with gradient fill
- add_gradient_background: Apply a gradient background to an entire slide
- add_solid_shape: Add a shape with solid color fill
- add_image_filled_shape: Add a shape filled with an image
- add_textbox: Add a text box with auto-resize font size
- add_line: Add a line to a slide
```

---

## 2. Code Generation

**节点**: `generate_slide_code_and_execute`

**智能体**: Coder

**用途**: 生成 python-pptx 代码创建幻灯片

**输入变量**:
- `{slide_title}` - 幻灯片标题
- `{enriched_points}` - 丰富后的要点
- `{slide_detail}` - 幻灯片布局描述
- `{style}` - 幻灯片风格
- `{main_color}`, `{accent_color}` - 颜色
- `{background_tone}`, `{heading_font_color}`, `{body_font_color}` - 字体颜色
- `{font_name}` - 字体名称
- `{style_summary}` - 风格描述
- `{images_json_embedded}` - 图片数据
- `{save_dir}` - 保存目录
- `{ppt_section.name}` - 章节名称
- `{slide_index}` - 幻灯片索引

**Prompt**:

```
Generate Python code that creates slides using the python-pptx library based on the following detailed slide description:

Title: {slide_title}
Detailed bullet points: {enriched_points}
Slide description: {slide_detail}
Slide style: {style}
Primary color: {main_color}
Accent color: {accent_color}
Background tone: {background_tone}
Heading font color: {heading_font_color}
Body font color: {body_font_color}
Font name: {font_name}
Style summary: {style_summary}
{images_json_embedded}

{ppt_tools_prompt}

Code requirements:
1. Import the necessary libraries.
2. Create the slides and ensure the widescreen standard aspect ratio: 16:9 (13.33 inches × 7.5 inches).
3. According to the detailed description, add the title, bullet points, and images at specified positions; set fonts and styles; explicitly set the size of each element to prevent overlap/occlusion; ensure text wraps automatically. The font size of the main text should be **at least 16**.
4. Only the provided image URLs can be used. Do not reserve any positions for any images that are not provided, and do not use text descriptions to fill the gaps. Or you can also manually create some flowcharts using various graphics, but don't just leave an empty space or just provide a textual description.
5. All the text should be placed on the top layer.
6. Save the file as: "{save_dir}/{ppt_section.name}_slide_{slide_index + 1}.pptx"

Previous code and errors (if any):
{previous_code}
{error_message}

Please provide complete, executable Python code based on this information. Note: output Python code only, do not output any other text.
Code will be save in utf-8 encoding.
```

---

## 3. Code Prefix

**智能体**: Coder

**用途**: Python 导入模板

```python
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.enum.shapes import MSO_AUTO_SHAPE_TYPE as MSO_SHAPE
from pptx_tools.add_free_shape import *
```

---

## 4. Completeness Evaluation

**节点**: `generate_slide_code_and_execute` (评估)

**智能体**: Designer

**用途**: 验证生成的代码是否与设计规范匹配

**Prompt**:

```
# Design:
{slide_detail}

# Code:
{python_code}

# Task:
You are an expert evaluator for Slide Design-to-Code generation tasks. You must assess whether the produced code accurately matches the provided slide design according to the following criteria:
(1) Every designed element appears in the code and is correctly configured in terms of color, position, shape, size, and other relevant attributes;
(2) Every requirement specified in the design specification is faithfully implemented in the code.

OUTPUT FORMAT:
{
   "Element Match": discription of correctly implemented design elements,
   "Element Mismatch": discription of incorrectly implemented or missing design elements,
   "Element Match Score": score (1-5, 1=poor, 5=excellent),
   "Requirement Compliance": discription of met requirements,
   "Requirement Non-Compliance": discription of unmet requirements,
   "Requirement Compliance Score": score (1-5, 1=poor, 5=excellent),
   "Total Score": total_score (average of above scores),
   "Suggesstions": improvement suggestions
}
```

---

## 5. Aesthetics Evaluation

**节点**: `ppt_slide_to_image_and_validate`

**智能体**: Designer

**用途**: 评估幻灯片视觉美学

**Prompt**:

```
You are a Slide Aesthetics Expert. Please evaluate the slide purely from a visual and aesthetic perspective (ignore content accuracy) across the following dimensions:

1. Layout & Composition
Whether the spatial arrangement is balanced, alignment is consistent, and spacing is appropriate.

2. Visual Hierarchy
Whether visual weight is properly distributed, key elements stand out, and the viewing flow feels natural.

3. Color & Contrast
Whether the color palette is harmonious, contrasts are sufficient, and overall color usage feels cohesive.

4. Typography
Whether font selection, sizes, spacing, and text layout are visually appealing and easy to read.

5. Whitespace & Balance
Whether negative space is appropriately used and the slide feels neither overcrowded nor empty.

6. Overall Aesthetic Consistency
Whether shapes, colors, fonts, and stylistic elements follow a coherent and unified aesthetic style.

Provide a score from 1 to 5 for each dimension.
```

---

## 6. Cover Evaluation

**智能体**: Designer

**用途**: 综合评估封面/结尾幻灯片

**Prompt**:

```
Evaluate cover slides across three dimensions:

A. Completeness (30 points)
- Design Element Consistency
- Content Fidelity

B. Compliance (30 points)
- Overlap Ratio
- Page Occupancy Ratio
- Overflow Ratio

C. Aesthetics (40 points)
- Layout & Composition
- Visual Hierarchy
- Color & Contrast
- Typography

Output format:
{
  "Total Score": float,
  "Breakdown": {
    "Completeness": float,
    "Compliance": float,
    "Aesthetics": float
  },
  "Suggestions": string
}
```
