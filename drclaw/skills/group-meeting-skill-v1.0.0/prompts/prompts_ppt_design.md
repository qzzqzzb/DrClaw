# PPT Design Prompts - PPT 设计阶段

本文档包含 **PPT 设计阶段** 的所有 prompts，对应工作流中的 `generate_ppt_styles` 和 `enrich_slide_content` 节点。

---

## 1. Style Plan

**节点**: `generate_ppt_styles`

**智能体**: Designer

**用途**: 为整个演示文稿创建统一的视觉风格指南

**输入变量**:
- `{topic}` - 演示主题
- `{style}` - 演示风格偏好

**Prompt**:

```
### Color Style Guide for Slide Design

Effective slide design relies on a clear and disciplined color system. A well-constructed palette generally includes one dominant color, one to two accent colors, and a set of neutral tones for balance.

Please provide a **detailed, cohesive description of the overall visual style** for the slide template, **no more than 200 words**.

Focus **only on stylistic and aesthetic elements** — **do NOT provide any guidance on layout or content placement.**

Your style description should cover:

### 1. Overall Style Overview
Describe the high-level visual tone, atmosphere, and aesthetic direction of the slide deck.

### 2. Decorative Graphic Motifs
Clearly specify the main decorative shapes used - colors, application methods, size range, density, typical layout tendencies.

### 3. Style Guidelines for Function Slides (Cover, Ending, Section Break Slides) (50 words)
Consist with the overall style, describe specific stylistic elements for function slides.

### 4. Style Guidelines for Standard Content Slides (50 words)
Consist with the overall style, describe specific stylistic elements for standard content slides.
```

---

## 2. Color Examples

**节点**: `generate_ppt_styles`

**智能体**: Designer

**用途**: 提供颜色搭配示例和设计指南

**Prompt**:

```
### **Color Style Guide for Slide Design**

Effective slide design relies on a clear and disciplined color system. A well-constructed palette generally includes one dominant color, one to two accent colors, and a set of neutral tones for balance. The dominant color establishes the visual identity of the slide, while the accent colors highlight key information such as keywords, icons, or numerical results. Neutral grays should be used for backgrounds, text blocks, and low-priority elements to prevent visual clutter.

For gradients, choose subtle transitions within the same hue family rather than mixing unrelated colors; this preserves consistency and avoids visual noise. When applying multiple colors on a single slide, maintain a contrast ratio that ensures text readability, especially on large screens. Limit the use of saturated colors to essential parts only, and avoid using more than one highly saturated accent color at the same time.

To preserve stylistic coherence across slides, apply the same color hierarchy to titles, body text, shapes, and background regions.


COLOR PALETTE EXAMPLES:
{
   "main_color": "#D6CCC2",
   "accent_color": "#B2967D",
   "background_tone": "light",
   "heading_font_color": "#3F3F46",
   "body_font_color": "#52525B"
},
{
   "main_color": "#553C9A",
   "accent_color": "#9F7AEA",
   "background_tone": "dark",
   "heading_font_color": "#FFFFFF",
   "body_font_color": "#E9D8FD"
},
{
   "main_color": "#F6E05E",
   "accent_color": "#553C9A",
   "background_tone": "light",
   "heading_font_color": "#1A202C",
   "body_font_color": "#2D3748"
},
```

---

## 3. Style Generation

**节点**: `generate_ppt_styles`

**智能体**: Designer

**用途**: 推荐适合演示的风格和颜色方案

**输入变量**:
- `{topic}` - 演示主题
- `{style}` - 用户期望的风格

**Prompt**:

```
You are an experienced presentation expert. You need to recommend a suitable style and color scheme for a presentation PPT.
Presentation topic: {topic}
User's expected presentation style: {style}
Please select the appropriate style and color scheme for the topic based on the provided information. And it is also important to ensure the harmony between the main color and the secondary color.
{color_examples_prompt}
Return **only JSON format**:
{
    "style": "Style",
    "main_color": "Recommended primary color",
    "accent_color": "Recommended accent color",
    "background_tone": "light/dark dominated + background color description",
    "heading_font_color": "recommended heading font color",
    "body_font_color": "recommended body font color"
    "font_name": "recommended font name"
}
```

---

## 4. Design Formatting (v1)

**节点**: `enrich_slide_content` (布局描述)

**智能体**: Designer

**用途**: 为每张幻灯片生成结构化的设计规范

**输入变量**:
- `{main_color}` - 主色
- `{accent_color}` - 强调色

**Prompt**:

```
# Canvas & Units
- Canvas: 13.33 × 7.5 inches (width × height)
- All coordinates and sizes are in **inches**, with 2 decimal places
- No elements may go out of bounds or overlap (except background textures/separators)
- In the Layout layer, every block uses **absolute positioning**: top-left `(x, y)`, size `(w, h)`

# General Requirements
- **Only the provided image URLs can be used. Do not reserve any positions for any images that are not provided.**
- **If there is no images provided, do not reference images in the design and do not leave extra space.**
- **The use of pictures or the creation of flowcharts is encouraged.**

# Layered Structure
## Background Layer
- One **natural-language summary** (≤ 80 words) describing style and visual tone
- One **machine-readable JSON**
- If Tone is light, use a light-colored background; If it is dark, use a dark background.
- The font size of the main text should be at least 16.

## Layout Layer
- Layout summary (single block / top-bottom / left-right / n horizontal blocks / 2×2 grid / card grid)
- One **natural-language description** (≤ 120 words) explaining layout logic
- A **structure JSON**: keys "Block1/Block2/…" with function, position, size

## Content Layer
- For each block id from the Layout layer, provide **text/image** content.
- The images should be scaled proportionally.

# Output Format
[Background]
  Preset background: none / <filename or URL>
  Tone: light/dark/neutral
  Primary/Accent: {main_color} / {accent_color}
  Base texture: <color + style, or "none">
  Edge ornament: <color + style, or "none">
  Natural-language description: <≤80 words>

[Layout]
  Summary: single / top-bottom / left-right / three-horizontal / 2x2-grid / card-grid / ...
  Natural-language details: <≤120 words>
  Structure: {blocks...}

[Content]
  {block_id: {text/image content...}}
```

---

## 5. Detail Prompt (Layout Description)

**节点**: `enrich_slide_content`

**智能体**: Designer

**用途**: 基于详细信息生成幻灯片布局描述

**输入变量**:
- `{slide_title}` - 幻灯片标题
- `{enriched_points}` - 丰富后的要点
- `{style}` - 幻灯片风格
- `{slide_layout}` - 幻灯片布局类型
- `{main_color}`, `{accent_color}` - 颜色
- `{background_tone}`, `{heading_font_color}`, `{body_font_color}` - 字体颜色
- `{font_name}` - 字体名称
- `{style_summary}` - 风格描述
- `{images_json_embedded}` - 可用图片

**Prompt**:

```
You are a seasoned slide designer responsible for designing slide layouts.
Generate a slide layout description in JSON format based on the following details:

Title: {slide_title}
Detailed points: {enriched_points}
Slide style: {style}
Slide layout: {slide_layout}
Primary color: {main_color}
Accent color: {accent_color}
Background tone: {background_tone}
Heading font color: {heading_font_color}
Body font color: {body_font_color}
Font name: {font_name}
Style summary: {style_summary}

Here are some images you may optionally use in the PPT:
<Image list>
{images_list}
</Image list>

{design_formatting_prompt}

<Suggestions for improving design>
{design_suggestions}
{aestheitcs_suggestions}
```

---

## 6. Design Evaluation

**节点**: `enrich_slide_content` (评估)

**智能体**: Designer

**用途**: 评估幻灯片设计质量

**Prompt**:

```
You are a Slide Layout Review Expert. Please evaluate the slide design based on the following dimensions.

A. Completeness: Whether all requested design requirements are properly reflected, and whether text and visuals match the provided content.

A.1 Design Element Consistency
Assess whether the color, style, font, and other requirements are correctly applied throughout the design.

A.2 Content Fidelity
Check whether all referenced text and images appear in the provided source content without omission or fabrication.

B. Compliance: Whether the designed visual and text blocks follow structural rules such as non-overlap and proper spatial arrangement.

B.1 Overlap Ratio
Check whether any text or visual elements unintentionally overlap with each other.

B.2 Page Occupancy Ratio
Evaluate whether the total occupied area of all elements is appropriate and whether the layout is visually balanced.

B.3 Overflow Ratio
Identify portions of any element that extend beyond the slide boundary.

Provide a score from 1 to 5 for each dimension.
```
