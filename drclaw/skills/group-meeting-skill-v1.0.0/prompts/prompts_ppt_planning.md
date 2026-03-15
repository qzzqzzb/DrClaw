# Planning Prompts

本文档包含 DeepSlides 系统中用于 **PPT 规划阶段** 的所有 prompts。

---

## 1. Storyline Prompt

**用途**: 确定演示文稿的风格、故事线和主题颜色

**智能体**: Planner Agent

**输入变量**:
- `{topic}` - 演示主题
- `{presentation_minutes}` - 演示时长（分钟）
- `{prefix}` - 用户期望的风格（可选）

**Prompt**:

```
You are an experienced presentation expert tasked with creating a presentation PPT. Now you need to determine the presentation's style, storyline, and theme colors (primary color + accent color).
Presentation topic: {topic}
Presentation duration: {presentation_minutes} minutes
{prefix}

Reference storyline templates:
- Problem-Solution: Clearly identify a core problem and provide clear, specific solutions.
- Situation-Conflict-Resolution-Outcome: First set up a scene, describe the challenge, offer the solution, and finally present positive results.
- SCQA (Situation-Complication-Question-Answer): Provide background information, introduce the complication, state the key question clearly, and give the answer.
- Timeline (Past-Present-Future): Present past events, current status, and future goals in chronological order.
- Contrast (Current vs. Future): Clearly contrast existing problems with the ideal future state, highlighting how to achieve the transformation.
- Pyramid: Start with the conclusion and unfold arguments layer by layer from top to bottom, reinforcing the core idea with rigorous, clear logic.
- Research Report: Introduce the topic within its field background, systematically outline the current status, methods, and challenges, and finally present future research trends and directions.

Return JSON format:
{
    "style": "Style",
    "storyline": "Storyline type",
    "main_color": "Recommended primary color",
    "accent_color": "Recommended accent color"
}
```

**输出**:
```json
{
    "style": "modern tech",
    "storyline": "Problem-Solution",
    "main_color": "#0066CC",
    "accent_color": "#FF6600"
}
```

---

## 2. PPT Length Prompt

**用途**: 根据演示时长推荐合适的幻灯片数量

**智能体**: Writer Agent

**输入变量**:
- `{topic}` - 演示主题
- `{presentation_minutes}` - 演示时长（分钟）

**Prompt**:

```
You are an experienced presentation expert.

Topic: {topic}
Presentation duration: {presentation_minutes} minutes

Please suggest an appropriate number of PPT slides for this duration
(each slide should have a moderate amount of content, not overcrowded;
one slide generally corresponds to about 1-2 minutes of presentation time).
JSON format: {"recommended_slides": 10}
```

**输出**:
```json
{
    "recommended_slides": 10
}
```

---

## 3. Section Distribution Prompt

**用途**: 重新规划 PPT 章节结构并分配各章节幻灯片数

**智能体**: Writer Agent

**输入变量**:
- `{topic}` - 演示主题
- `{style}` - 风格
- `{storyline}` - 故事线
- `{recommended_slides}` - 推荐的幻灯片总数

**Prompt**:

```
Presentation topic: {topic}
Style: {style}
Storyline: {storyline}
Recommended total slides: {recommended_slides}

Attention: Do NOT create a "Q&A" or "Closing" section.

Based on the above information, re-plan the PPT section structure and allocate the number of slides for each section. Return in JSON format, for example:
{
    "section_distribution": {
        "Introduction": 2,
        "Methodology": 3,
        "Results": 3,
        "Conclusion": 2
    }
}
```

**输出**:
```json
{
    "section_distribution": {
        "Introduction": 2,
        "Methodology": 3,
        "Results": 3,
        "Conclusion": 2
    }
}
```

---

## 4. PPT Outline Prompt

**用途**: 生成 PPT 大纲（每张幻灯片的标题和要点）

**智能体**: Writer Agent

**输入变量**:
- `{topic}` - 演示主题
- `{style}` - 风格
- `{storyline}` - 故事线
- `{state["final_report"]}` - 报告内容
- `{section_distribution}` - 章节分配

**Prompt**:

```
You excel at designing slide outlines for presentations.
Presentation topic: {topic}
Style: {style}
Storyline: {storyline}

Reference material for the presentation:
{final_report}

The slide allocation for each section is as follows:
{section_distribution}

Please generate a PPT outline that adheres to the above slide allocation. Each slide should include:
- A title
- key points

Important: **Do NOT make every slide contain exactly 3 or 4 key points.**
Ensure variety by creating some slides with **5** key points, and some with **6**.

JSON format:
Return JSON only, e.g.:
{
    "ppt_sections": [
        {
        "name": "Introduction",
        "allocated_slides": 2,
        "slides": [
            {"title":"Sample 6-point slide","points":["A","B","C","D","E","F"]},
            {"title":"Sample 4-point slide","points":["A","B","C","D"]},
            {"title":"Sample 5-point slide","points":["A","B","C","D","E"]},
            {"title":"Sample 3-point slide","points":["A","B","C"]}
        ]
        }
    ]
}
```

**输出**:
```json
{
    "ppt_sections": [
        {
            "name": "Introduction",
            "allocated_slides": 2,
            "slides": [
                {"title": "Background", "points": ["Point 1", "Point 2", "Point 3"]},
                {"title": "Objectives", "points": ["Goal 1", "Goal 2", "Goal 3", "Goal 4"]}
            ]
        },
        {
            "name": "Methodology",
            "allocated_slides": 3,
            "slides": [
                {"title": "Approach", "points": ["Method A", "Method B", "Method C"]},
                {"title": "Data Collection", "points": ["Source 1", "Source 2", "Source 3", "Source 4", "Source 5"]},
                {"title": "Analysis", "points": ["Technique 1", "Technique 2", "Technique 3", "Technique 4"]}
            ]
        }
    ]
}
```

---

## 5. Content Enrichment Prompt

**用途**: 扩展幻灯片内容，为每个要点提供详细描述

**智能体**: Writer Agent

**输入变量**:
- `{topic}` - 演示主题
- `{ppt_section_name}` - 章节名称
- `{slide_title}` - 幻灯片标题
- `{slide_points}` - 幻灯片要点列表
- `{source_str}` - 搜索结果

**Prompt**:

```
Based on the following points, expand the slide content. Provide a detailed description for each point and ensure it is linked to the search results.

Slide topic: {topic}
Slide section: {ppt_section_name}
Slide title: {slide_title}
Points: {slide_points}

Search results:
{source_str}

Please expand and describe each point in detail, suitable for a presentation. Keep the language concise yet informative—ideally, each expanded point should not exceed **30 words**. Return the result in JSON format only; do not output any other text:

{
    "enriched_points": [
        {"point_title": "Point Title 1", "expanded_content": "Expanded content 1"},
        {"point_title": "Point Title 2", "expanded_content": "Expanded content 2"},
        {"point_title": "Point Title 3", "expanded_content": "Expanded content 3"},
        ...
    ]
}
```

**输出**:
```json
{
    "enriched_points": [
        {"point_title": "Background", "expanded_content": "Research shows that AI adoption has grown by 300% in the past 5 years."},
        {"point_title": "Problem Statement", "expanded_content": "However, 60% of AI projects still fail due to poor implementation."},
        {"point_title": "Our Solution", "expanded_content": "We propose a novel framework that improves success rate by 85%."}
    ]
}
```

---

## 6. Query Writer for PPT

**用途**: 为 PPT 幻灯片内容生成搜索查询

**智能体**: Writer Agent

**输入变量**:
- `{topic}` - 演示主题
- `{section_topic}` - 章节主题
- `{slide_title}` - 幻灯片标题

**Prompt**:

```
You are an assistant highly skilled at generating relevant search queries for presentation slides.

<Slide Topic>
{topic}
</Slide Topic>

<Current Section Topic>
{section_topic}
</Current Section Topic>

<Current Slide Topic>
Title: {slide_title}
</Current Slide Topic>

<Slide Bullet Points>
{slide_content}
</Slide Bullet Points>
```
