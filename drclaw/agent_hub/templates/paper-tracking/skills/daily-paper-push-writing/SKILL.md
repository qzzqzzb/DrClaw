---
name: daily-paper-push-writing
description: A research/push notification writing guide. Use this skill with high priority when users ask you to perform tasks like daily paper push.
---

# daily-paper-push-writing — Agent Skill Reference

`daily-paper-push-writing` 是一个写作技能，提供一种生成每日科研论文汇总的规范化写作流程，从而帮助用户高效获取特定领域的最新研究成果和重要信息。

## 写作原则

### 论文筛选原则
- **时效性优先**：优先选择近 1-2 周内发布的论文
- **相关性过滤**：紧扣用户关注的领域和关键词
- **质量排序**：按引用量、作者影响力、实验完整性等综合评估
- **多样性考量**：兼顾不同研究方向和方法论，避免内容过度集中

### 写作风格原则
- **简洁精准**：摘要提炼核心贡献，控制在 100-150 字
- **客观中立**：如实描述论文内容，避免过度主观评价
- **学术规范**：使用规范的学术用语，标题、作者、链接等信息准确无误
- **价值导向**：在"学术价值分析"部分侧重实际应用场景和方法论借鉴意义

### 内容组织原则
- **层次分明**：每篇论文遵循统一的格式模板
- **重点突出**：用加粗或 emoji 标注关键信息（创新点、结论）
- **逻辑连贯**：简报整体按论文重要程度或主题相关性排序

### 读者价值原则
- **降低阅读门槛**：帮助读者快速判断论文是否值得深入阅读
- **提供增量价值**：不仅罗列摘要，还要有对研究趋势的洞察
- **可操作性强**：链接直达，方便读者进一步探索

### 长期运营原则
- **建立运营日志**：由于该skill常用于长期任务，应该在memory中建立日志，记录每次抓取的论文和用户反馈，避免推送重复内容，并根据用户反馈不断优化筛选和写作流程。
- **注重推送质量**：每次推送的论文建议控制在四到八篇，综合考虑论文的质量、相关性和潜在价值，确保每次推送都能为用户提供有价值的信息，避免信息过载。



## 写作模板
📢 **【今日论文速递】20XX年XX月XX日** 📢
---

> 📚 **领域**：XXX | 📊 **关键词**：关键词1、关键词2、关键词3

---

🌟 **No.1** 📄 **论文标题**
>
> 📝 **作者**：[作者列表]
> 📅 **发布时间**：20XX年XX月
> 🔗 **论文链接**：👉 [arXiv/论文链接]
> 🏷️ **arXiv ID**：arXiv:XXXX.XXXXX
> 📋 **摘要**：[论文摘要...]
>
> 📝 **Overview 精华**（由 LLM 从 PDF 文本提取）：
> > [LLM 提取的论文核心观点、研究动机、关键贡献...]
>
> 📊 **实验结果图表**：![figure1](./images/001_figure1.jpg)
>
> 💡 **学术价值分析**：简要分析该论文的研究创新点、实验方法、潜在应用价值～

---

🌟 **No.2** 📄 **论文标题**
>
> 📝 **作者**：[作者列表]
> 📅 **发布时间**：20XX年XX月
> 🔗 **论文链接**：👉 [arXiv/论文链接]
> 🏷️ **arXiv ID**：arXiv:XXXX.XXXXX
> 📋 **摘要**：[论文摘要...]
>
> 📝 **Overview 精华**（由 LLM 从 PDF 文本提取）：
> > [LLM 提取的论文核心观点、研究动机、关键贡献...]
>
> 📊 **实验结果图表**：![figure1](./images/002_figure1.jpg)
>
> 💡 **学术价值分析**：简要分析该论文的研究创新点、实验方法、潜在应用价值～

---

🌟 **No.3** 📄 **论文标题**
>
> 📝 **作者**：[作者列表]
> 📅 **发布时间**：20XX年XX月
> 🔗 **论文链接**：👉 [arXiv/论文链接]
> 🏷️ **arXiv ID**：arXiv:XXXX.XXXXX
> 📋 **摘要**：[论文摘要...]
>
> 📝 **Overview 精华**（由 LLM 从 PDF 文本提取）：
> > [LLM 提取的论文核心观点、研究动机、关键贡献...]
>
> 📊 **实验结果图表**：![figure1](./images/003_figure1.jpg)
>
> 💡 **学术价值分析**：简要分析该论文的研究创新点、实验方法、潜在应用价值～

---

🌟 **No.4** 📄 **论文标题**
>
> 📝 **作者**：[作者列表]
> 📅 **发布时间**：20XX年XX月
> 🔗 **论文链接**：👉 [arXiv/论文链接]
> 🏷️ **arXiv ID**：arXiv:XXXX.XXXXX
> 📋 **摘要**：[论文摘要...]
>
> 📝 **Overview 精华**（由 LLM 从 PDF 文本提取）：
> > [LLM 提取的论文核心观点、研究动机、关键贡献...]
>
> 📊 **实验结果图表**：![figure1](./images/004_figure1.jpg)
>
> 💡 **学术价值分析**：简要分析该论文的研究创新点、实验方法、潜在应用价值～

---

🌟 **No.5** 📄 **论文标题**
>
> 📝 **作者**：[作者列表]
> 📅 **发布时间**：20XX年XX月
> 🔗 **论文链接**：👉 [arXiv/论文链接]
> 🏷️ **arXiv ID**：arXiv:XXXX.XXXXX
> 📋 **摘要**：[论文摘要...]
>
> 📝 **Overview 精华**（由 LLM 从 PDF 文本提取）：
> > [LLM 提取的论文核心观点、研究动机、关键贡献...]
>
> 📊 **实验结果图表**：![figure1](./images/005_figure1.jpg)
>
> 💡 **学术价值分析**：简要分析该论文的研究创新点、实验方法、潜在应用价值～

---

## 论文素材获取

每篇论文需要获取：
1. **PDF 文本** → 供 LLM 提取 Overview 精华内容
2. **实验结果图表** → 论文中的主要实验结果图

### 工作流程

```
┌─────────────────────────────────────────────────────────────┐
│  Step 1: 下载 PDF (一次下载，后续复用)                       │
│  python scripts/pdf_download.py <arxiv_id> [output_dir]    │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 2: PDF 转文本 (供 LLM 读取 Overview)                 │
│  python scripts/pdf_to_text.py <pdf> <output.txt>          │
└─────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────┐
│  Step 3: 提取图表 (默认第1张 = 架构图)                      │
│  python scripts/pdf_figure_capture.py <pdf> <output.png>   │
└─────────────────────────────────────────────────────────────┘
```

### 脚本 1：PDF 下载

```bash
python scripts/pdf_download.py <arxiv_id> [output_dir]
```

**示例：**
```bash
# 下载到默认 ./pdfs/ 目录
python scripts/pdf_download.py 1706.03762

# 下载到指定目录
python scripts/pdf_download.py 1706.03762 ./my_pdfs/

# 强制重新下载
python scripts/pdf_download.py 1706.03762 --force
```

---

### 脚本 2：PDF 转文本

```bash
python scripts/pdf_to_text.py <arxiv_id|pdf_path> <output_path> [options]
```

**示例：**
```bash
# 转换为完整文本（通过 arXiv ID - 会自动下载）
python scripts/pdf_to_text.py 1706.03762 paper.txt

# 使用本地 PDF（更快）
python scripts/pdf_to_text.py ./pdfs/1706.03762.pdf overview.txt

# 只提取 Overview/Introduction 部分
python scripts/pdf_to_text.py ./pdfs/1706.03762.pdf overview.txt --section overview

# 只提取前 5 页
python scripts/pdf_to_text.py ./pdfs/1706.03762.pdf output.txt --pages 5
```

**可选参数：**
- `--section, -s`：提取特定章节（overview, method, experiment）
- `--pages, -p`：提取前 N 页

**用途**：将文本提供给 LLM，让模型提取 Overview 精华内容，写入推送文档。

---

### 脚本 3：图表截取

```bash
python scripts/pdf_figure_capture.py <arxiv_id|pdf_path> <output_path> [options]
```

**示例：**
```bash
# 使用本地 PDF（更快，无需重复下载）
python scripts/pdf_figure_capture.py ./pdfs/1706.03762.pdf images/001_figure.jpg --figure 1

# 列出论文中所有图表
python scripts/pdf_figure_capture.py ./pdfs/1706.03762.pdf --list

# 截取第 1 张图表（默认架构图）
python scripts/pdf_figure_capture.py ./pdfs/1706.03762.pdf images/001_figure.jpg --figure 1

# 截取第 3 页的所有图表
python scripts/pdf_figure_capture.py ./pdfs/1706.03762.pdf images/ --page 3
```

**可选参数：**
- `--dpi, -r`：分辨率，默认 150 DPI
- `--list, -l`：列出论文中所有图表（不截取）
- `--figure, -f`：指定要截取的图表编号
- `--page, -p`：截取指定页面的所有图表

---

### 依赖说明

所有脚本都依赖以下 Python 包：
- `requests`：下载 PDF
- `pymupdf` (fitz)：解析 PDF

```bash
pip install requests pymupdf
```

### 注意事项

- **推荐工作流**：先 `pdf_download` 下载一次，然后所有操作都使用本地 PDF 路径
- 图表建议选择包含关键数据可视化的图表（如折线图、柱状图等）
- 使用 `--list` 可以先查看论文有哪些图表，再决定截取哪个

---

## 最终输出文件结构

最终输出为一个文件夹，其结构如下：

```
{datetime}_paper_push/
├── images/
│   ├── 001_figure1.jpg          # 论文1：实验结果图表
│   ├── 002_figure1.jpg          # 论文2：实验结果图表
│   ├── 003_figure1.jpg          # 论文3：实验结果图表
│   ├── 004_figure1.jpg          # 论文4：实验结果图表
│   └── 005_figure1.jpg          # 论文5：实验结果图表
└── paper_push.md                 # 推送正文（Markdown 格式）
```

**注意**：任务完成后请删除 PDF 文件，不在本地缓存。

**注意**：Overview 内容由 LLM 从 PDF 文本提取，直接写入 Markdown 正文中，无需单独的图片文件。

**MD 正文中图片引用使用相对路径：**
```markdown
![figure1](./images/001_figure1.jpg)
```

---

## 工作流程

1. **获取论文列表**：先调用 `arxiv-watcher` skill 获取目标领域的最新论文
2. **筛选论文**：根据时效性、相关性、质量等原则筛选 4-8 篇论文
3. **下载 PDF**：对每篇论文调用 `pdf_download.py` 下载 PDF 到本地
4. **提取文本**：对每篇论文调用 `pdf_to_text.py` 获取文本，**将文本提供给 LLM 让其提取 Overview 精华**
5. **提取图表**：对每篇论文调用 `pdf_figure_capture.py` 获取主要实验结果图表（默认第1张）
6. **撰写正文**：按照写作模板组织内容，每篇论文附带 LLM 提取的 Overview 精华和图表引用
7. **输出文件夹**：创建以日期命名的文件夹，包含 images/ 和 paper_push.md
8. **清理 PDF**：任务完成后**删除 pdfs/ 目录及其内容**，不缓存 PDF 文件

