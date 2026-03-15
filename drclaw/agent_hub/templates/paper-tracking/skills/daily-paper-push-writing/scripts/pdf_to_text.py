#!/usr/bin/env python3
"""
PDF to Text Converter

Converts arXiv PDF to text, with optional extraction of specific sections.
Useful for LLM to read paper content.

Usage:
    python pdf_to_text.py <arxiv_id|pdf_path> [output_path] [options]

Examples:
    # Convert PDF to text (by arXiv ID - will download first)
    python pdf_to_text.py 1706.03762 paper.txt

    # Use local PDF file
    python pdf_to_text.py ./pdfs/1706.03762.pdf output.txt

    # Extract only Overview/Introduction section
    python pdf_to_text.py 1706.03762 overview.txt --section overview

    # Extract first N pages
    python pdf_to_text.py 1706.03762 output.txt --pages 5
"""

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional, List, Tuple

import fitz  # PyMuPDF
import requests


class PDFToText:
    """Convert PDF to text."""

    # Section headers to detect
    SECTION_PATTERNS = {
        'overview': [
            r'^1\s+Overview$',
            r'^1\s+Introduction$',
            r'^Overview$',
            r'^Introduction$',
        ],
        'method': [
            r'^(\d+\s+)?Method',
            r'^(\d+\s+)?Methodology',
            r'^(\d+\s+)?Model',
            r'^(\d+\s+)?Approach',
        ],
        'experiment': [
            r'^(\d+\s+)?Experiment',
            r'^(\d+\s+)?Experiments',
            r'^(\d+\s+)?Evaluation',
            r'^(\d+\s+)?Results',
        ],
    }

    def __init__(
        self,
        pdf_source: str,
        output_path: Optional[str] = None,
        dpi: int = 150,
        timeout: int = 60,
        section: Optional[str] = None,
        pages: Optional[int] = None,
    ):
        self.pdf_source = pdf_source
        self.output_path = Path(output_path) if output_path else None
        self.dpi = dpi
        self.timeout = timeout
        self.section = section
        self.pages = pages

    def _is_arxiv_id(self, source: str) -> bool:
        """Check if source is arXiv ID or file path."""
        # If it looks like an arXiv ID (no slashes, no .pdf extension)
        if '/' not in source and not source.endswith('.pdf'):
            # Check if it matches arXiv ID pattern
            return bool(re.match(r'^\d{4}\.\d{4,5}(v\d+)?$', source))
        return False

    def _download_arxiv_pdf(self, arxiv_id: str) -> bytes:
        """Download PDF from arXiv."""
        # Normalize arXiv ID
        arxiv_id = re.sub(r'^arxiv:', '', arxiv_id, flags=re.IGNORECASE)
        arxiv_id = re.sub(r'\.pdf$', '', arxiv_id, flags=re.IGNORECASE)

        url = f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        print(f"⬇️  Downloading: {url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()
        return response.content

    def _read_pdf_file(self, path: str) -> bytes:
        """Read PDF from local file."""
        with open(path, 'rb') as f:
            return f.read()

    def _find_section_pages(self, doc: fitz.Document, section: str) -> Tuple[int, int]:
        """Find the page range for a specific section."""
        patterns = self.SECTION_PATTERNS.get(section.lower(), [])

        start_page = 0
        end_page = doc.page_count - 1

        for page_num, page in enumerate(doc):
            text = page.get_text()

            for pattern in patterns:
                if re.search(pattern, text, re.MULTILINE | re.IGNORECASE):
                    start_page = page_num
                    print(f"📍 Found section '{section}' at page {page_num + 1}")

                    # Find next section to determine end
                    for next_page in range(page_num + 1, min(page_num + 6, doc.page_count)):
                        next_text = doc[next_page].get_text()
                        # Check for any numbered section
                        if re.search(r'^\d+\s+\w+', next_text, re.MULTILINE):
                            end_page = next_page - 1
                            break

                    return (start_page, end_page)

        # If section not found, return first N pages
        if section.lower() == 'overview':
            end_page = min(3, doc.page_count - 1)
        return (start_page, end_page)

    def _page_to_text(self, page: fitz.Page) -> str:
        """Extract text from a single page."""
        text = page.get_text()
        # Clean up text
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def convert(self) -> str:
        """Convert PDF to text."""
        # Get PDF content
        if self._is_arxiv_id(self.pdf_source):
            pdf_content = self._download_arxiv_pdf(self.pdf_source)
        else:
            pdf_content = self._read_pdf_file(self.pdf_source)

        # Open PDF
        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_content)
            tmp_path = tmp.name

        try:
            doc = fitz.open(tmp_path)

            # Determine page range
            if self.pages:
                start_page = 0
                end_page = min(self.pages - 1, doc.page_count - 1)
            elif self.section:
                start_page, end_page = self._find_section_pages(doc, self.section)
            else:
                start_page = 0
                end_page = doc.page_count - 1

            print(f"📄 Extracting pages {start_page + 1} to {end_page + 1}")

            # Extract text
            all_text = []
            for page_num in range(start_page, end_page + 1):
                page = doc[page_num]
                text = self._page_to_text(page)
                all_text.append(f"--- Page {page_num + 1} ---")
                all_text.append(text)

            full_text = "\n\n".join(all_text)
            doc.close()

        finally:
            os.unlink(tmp_path)

        # Output
        if self.output_path:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.output_path, 'w', encoding='utf-8') as f:
                f.write(full_text)
            print(f"✅ Saved: {self.output_path}")
        else:
            # Print to stdout
            print("\n" + "=" * 50)
            print(full_text[:5000])  # Limit output
            if len(full_text) > 5000:
                print(f"\n... (truncated, total {len(full_text)} chars)")

        return full_text

    def run(self) -> str:
        """Execute the conversion."""
        try:
            return self.convert()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"❌ Error: arXiv paper not found: {self.pdf_source}")
                sys.exit(1)
            else:
                print(f"❌ HTTP Error: {e}")
                sys.exit(1)
        except FileNotFoundError:
            print(f"❌ Error: PDF file not found: {self.pdf_source}")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Convert arXiv PDF to text',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('pdf_source', help='arXiv ID (e.g., 1706.03762) or PDF file path')
    parser.add_argument('output_path', nargs='?', help='Output text file path (optional)')
    parser.add_argument('--timeout', '-t', type=int, default=60,
                        help='Download timeout in seconds (default: 60)')
    parser.add_argument('--section', '-s',
                        help='Extract specific section: overview, method, experiment')
    parser.add_argument('--pages', '-p', type=int,
                        help='Extract first N pages')

    args = parser.parse_args()

    converter = PDFToText(
        pdf_source=args.pdf_source,
        output_path=args.output_path,
        timeout=args.timeout,
        section=args.section,
        pages=args.pages,
    )

    converter.run()


if __name__ == '__main__':
    main()
