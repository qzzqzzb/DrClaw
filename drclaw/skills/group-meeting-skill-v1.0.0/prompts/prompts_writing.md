# Writing Prompts - 写作阶段

本文档包含**写作阶段**的所有 prompts，对应工作流中的 `write_section` 和 `write_final_sections` 节点。

---

## 1. Section Writer

**节点**: `write_section`

**智能体**: Writer

**用途**: 基于研究结果撰写报告章节内容

**输入变量**:
- `{topic}` - 报告主题
- `{section_name}` - 章节名称
- `{section_topic}` - 章节主题
- `{section_content}` - 现有内容 (可选)
- `{context}` - 搜索结果/来源
- `{images_data}` - 可用图片列表

**Prompt**:

```
Write one section of a research report.

<Task>
1. Carefully read the report topic, section name, and section theme.
2. If existing section content is provided, review it.
3. Then examine the provided sources.
4. Decide which sources you will use to write the report section.
5. If an image list is provided, evaluate each image's relevance and select the most suitable one.
6. Write the report section and choose an illustration.
7. List the sources for the section at the end.
</Task>

<Writing Guidelines>
- If the existing section content is empty, write it from scratch.
- If existing content is present, integrate it with the sources.
- Strictly limit the length to 150-200 words.
- Use clear, simple language.
- Use short paragraphs (no more than 2-3 sentences).
- Use "##" for the section heading (Markdown format).
</Writing Guidelines>

<Image Handling Guidelines>
- If an image list is provided (up to 6 images), choose the one that best supports the section content.
- Image-selection format:
  ```image_selection
  {
    "selected_image_index": index of the chosen image (starting at 0),
    "reason": "Why this image was chosen",
    "caption": "A brief caption for the image"
  }
  ```
- If no suitable image exists, set "selected_image_index" to -1.
- The section content must stand alone; do not reference "as shown in the image" in the text.
</Image Handling Guidelines>

<Citation Rules>
- Assign a reference number to each unique URL.
- End with ### Sources and list each source with its number.
- Number sources sequentially (1, 2, 3, 4 …) with no gaps.
- Example format:
  [1] Source Title: URL
  [2] Source Title: URL
</Citation Rules>

<Final Checks>
1. Verify each point is supported by the provided sources.
2. Confirm each URL appears only once in the source list.
3. Ensure sources are numbered in order with no gaps.
4. Make sure the image-selection block follows the required JSON format.
</Final Checks>
```

---

## 2. Section Grader

**节点**: `write_section` (评估)

**智能体**: Writer

**用途**: 评估章节内容是否充分覆盖主题

**输入变量**:
- `{topic}` - 报告主题
- `{section_topic}` - 章节主题
- `{section}` - 章节内容
- `{number_of_follow_up_queries}` - 后续查询数量

**Prompt**:

```
Review a report section against the specified topic:

<Report Topic>
{topic}
</Report Topic>

<Section Theme>
{section_topic}
</Section Theme>

<Section Content>
{section}
</Section Content>

<Task>
Evaluate whether the section content adequately covers the section theme.

If the content does not sufficiently cover the theme, generate {number_of_follow_up_queries} follow-up search queries to gather the missing information.
</Task>
```

---

## 3. Final Section Writer

**节点**: `write_final_sections`

**智能体**: Writer

**用途**: 撰写引言和结论部分

**输入变量**:
- `{topic}` - 报告主题
- `{section_name}` - 章节名称 (Introduction/Conclusion)
- `{section_topic}` - 章节主题
- `{context}` - 所有可用的报告内容

**Prompt**:

```
You are a professional technical writing expert tasked with composing a chapter that synthesizes the remaining information for the report.

<Report Topic>
{topic}
</Report Topic>

<Section Name>
{section_name}
</Section Name>

<Section Theme>
{section_topic}
</Section Theme>

<Available Report Content>
{context}
</Available Report Content>

<Task>
Section-specific guidance:

For the **Introduction**:
- Use "#" for the report title (Markdown format).
- Limit to **50-100 words**.
- Use clear, straightforward language.
- Focus on the core motivation of the report in 1-2 paragraphs.
- Do **not** include structural elements (no lists or tables).
- No sources section required.

For the **Conclusion/Summary**:
- Use "##" for the section heading (Markdown format).
- Limit to **100-150 words**.
- For comparative reports: **Must** include a concise comparison table using Markdown table syntax.
- For non-comparative reports: Include **one** structural element only if it helps distill key points (table or list).
- End with concrete next steps or implications.
- No sources section required.

Writing approach:
- Use specific details rather than general statements.
- Make every word count.
- Concentrate on your most important points.
</Task>

<Quality Check>
- **Introduction**: 50-100 words, "#" as title, no structural elements, no sources section.
- **Conclusion**: 100-150 words, "##" as heading, at most one structural element, no sources section.
- Use Markdown formatting.
- Do **not** include word counts or any preamble in your reply.
</Quality Check>
```