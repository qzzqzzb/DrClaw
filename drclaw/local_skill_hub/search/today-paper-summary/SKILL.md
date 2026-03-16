---
name: today-paper-summary
description: Retrieves papers the user browsed today, downloads PDFs, generates summaries, and returns an enriched list. Use when the user asks what papers they read today, wants a summary of today's papers, or asks about their recent reading activity.
---

# today-paper-summary

Collect academic papers visited in the browser today, download their PDFs, summarize each one, and return structured results.

## Workflow

### Step 1: Fetch today's paper URLs

```python
from browser_history import collect_today_paper_urls
records = collect_today_paper_urls()
```

`records` is a `List[Dict]` with fields: `browser`, `db_path`, `url`, `title`, `visit_count`, `last_visit_time`.

### Step 2: Download PDFs

- Save directory: `<working_dir>/<today's date>/`, e.g. `2026-03-11/`
- Filename: use the paper ID or a slug of the title, e.g. `2603.09973.pdf`
- If `url` already points to a PDF, use it directly; if it's an abstract page, construct the PDF URL (for arXiv, replace `/abs/` with `/pdf/`)
- Use `requests` with a 30s timeout; on failure, record `download_error` and continue

```python
import requests
from pathlib import Path
from datetime import date

save_dir = Path(date.today().isoformat())
save_dir.mkdir(exist_ok=True)

def get_pdf_url(url: str) -> str:
    """Convert an abstract URL to a PDF URL (arXiv-specific)."""
    return url.replace("/abs/", "/pdf/")

def download_pdf(pdf_url: str, dest: Path) -> bool:
    try:
        r = requests.get(pdf_url, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 200:
            dest.write_bytes(r.content)
            return True
    except Exception:
        pass
    return False
```

### Step 3: Extract text and summarize

- Use `pypdf` to read the PDF — extract the first 5 pages (sufficient to cover the abstract and introduction)
- Use LLM with the prompt below, asking for a structured summary

```python
import pypdf

def extract_text(pdf_path: Path, max_pages: int = 5) -> str:
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(p.extract_text() or "" for p in reader.pages[:max_pages])
```

Summary prompt template:

```
Summarize the following paper in concise English using this format:

[Core contribution] One sentence (≤ 50 words)
[Method highlights]
- ...
[Key findings]
- ...

Paper content:
{text}
```

### Step 4: Build the enriched list

```python
results = []
for r in records:
    results.append({
        "browser":         r["browser"],
        "last_visit_time": r["last_visit_time"],
        "title":           r["title"],
        "url":             r["url"],
        "summary":         "<summary from Step 3, or 'unavailable' on failure>",
    })
```

Return `results`.

## Notes

- Install dependencies if needed: `pip install pypdf requests`
- Some publisher PDFs require institutional access; the download returns a login page instead of the actual PDF — set `summary` to `"PDF requires authorized access"` in that case
- arXiv PDFs are openly accessible and require no login
- Keep downloaded PDFs in the date folder for the user to review
