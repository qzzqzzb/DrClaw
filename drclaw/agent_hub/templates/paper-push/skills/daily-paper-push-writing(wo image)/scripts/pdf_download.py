#!/usr/bin/env python3
"""
PDF Downloader

Downloads arXiv papers to local directory for subsequent processing.
Caches PDFs to avoid repeated downloads.

Usage:
    python pdf_download.py <arxiv_id> [output_dir]

Examples:
    # Download to default ./pdfs/ directory
    python pdf_download.py 1706.03762

    # Download to specific directory
    python pdf_download.py 1706.03762 ./my_pdfs/

    # Force re-download even if exists
    python pdf_download.py 1706.03762 --force
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Optional

import requests


class PDFDownloader:
    """Download arXiv PDFs."""

    ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"

    def __init__(
        self,
        arxiv_id: str,
        output_dir: str = "./pdfs",
        timeout: int = 60,
        force: bool = False,
    ):
        self.arxiv_id = self._normalize_arxiv_id(arxiv_id)
        self.output_dir = Path(output_dir)
        self.timeout = timeout
        self.force = force

        # Create output directory
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _normalize_arxiv_id(self, arxiv_id: str) -> str:
        """Normalize arXiv ID."""
        arxiv_id = re.sub(r'^arxiv:', '', arxiv_id, flags=re.IGNORECASE)
        arxiv_id = re.sub(r'\.pdf$', '', arxiv_id, flags=re.IGNORECASE)
        return arxiv_id.strip()

    def _get_pdf_url(self) -> str:
        """Build the PDF download URL."""
        return self.ARXIV_PDF_URL.format(arxiv_id=self.arxiv_id)

    def _get_output_path(self) -> Path:
        """Get the output PDF path."""
        return self.output_dir / f"{self.arxiv_id}.pdf"

    def download(self) -> Path:
        """Download the PDF."""
        output_path = self._get_output_path()

        # Check if already exists
        if output_path.exists() and not self.force:
            print(f"📦 PDF already exists: {output_path}")
            return output_path

        url = self._get_pdf_url()
        print(f"⬇️  Downloading: {url}")

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

        response = requests.get(url, headers=headers, timeout=self.timeout)
        response.raise_for_status()

        # Save PDF
        with open(output_path, 'wb') as f:
            f.write(response.content)

        print(f"✅ Saved: {output_path}")
        return output_path

    def run(self) -> Path:
        """Execute the download."""
        try:
            return self.download()
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                print(f"❌ Error: arXiv paper not found: {self.arxiv_id}")
                sys.exit(1)
            else:
                print(f"❌ HTTP Error: {e}")
                sys.exit(1)
        except requests.exceptions.Timeout:
            print(f"❌ Error: Connection timed out after {self.timeout}s")
            sys.exit(1)
        except Exception as e:
            print(f"❌ Error: {e}")
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Download arXiv PDF',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('arxiv_id', help='arXiv paper ID (e.g., 1706.03762)')
    parser.add_argument('output_dir', nargs='?', default='./pdfs',
                        help='Output directory (default: ./pdfs)')
    parser.add_argument('--timeout', '-t', type=int, default=60,
                        help='Download timeout in seconds (default: 60)')
    parser.add_argument('--force', '-f', action='store_true',
                        help='Force re-download even if file exists')

    args = parser.parse_args()

    downloader = PDFDownloader(
        arxiv_id=args.arxiv_id,
        output_dir=args.output_dir,
        timeout=args.timeout,
        force=args.force,
    )

    downloader.run()


if __name__ == '__main__':
    main()
