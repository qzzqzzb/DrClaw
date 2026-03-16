#!/usr/bin/env python3
"""
PDF Figure Capture

Captures figure(s) from arXiv PDF or local PDF file.
Useful for creating paper push notifications with experiment result previews.

Usage:
    python pdf_figure_capture.py <arxiv_id|pdf_path> <output_path> [options]

Examples:
    # List all figures (by arXiv ID - will download first)
    python pdf_figure_capture.py 1706.03762 --list

    # Capture figure 1 (by arXiv ID)
    python pdf_figure_capture.py 1706.03762 images/001_figure.jpg --figure 1

    # Use local PDF file (faster - no re-download)
    python pdf_figure_capture.py ./pdfs/1706.03762.pdf images/figure.jpg --figure 1

    # Capture figures on specific page
    python pdf_figure_capture.py ./pdfs/1706.03762.pdf images/figure.jpg --page 3
"""

import argparse
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Optional, List, Dict, Tuple

import fitz  # PyMuPDF
import requests


class PDFFigureCapture:
    """Capture figures from arXiv PDF."""

    ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"

    # Patterns to detect figure captions
    FIGURE_PATTERNS = [
        r'^Figure\s+(\d+)',
        r'^Fig\.\s*(\d+)',
        r'^Fig\s+(\d+)',
        r'^图\s*(\d+)',
    ]

    # Sections that typically contain main experiment results
    EXPERIMENT_SECTIONS = [
        r'^(\d+\s+)?Experiment',
        r'^(\d+\s+)?Experiments',
        r'^(\d+\s+)?Evaluation',
        r'^(\d+\s+)?Results',
        r'^(\d+\s+)?Main\s+Results',
    ]

    def __init__(
        self,
        pdf_source: str,
        output_path: str,
        dpi: int = 150,
        timeout: int = 60,
        figure_num: Optional[int] = None,
        page: Optional[int] = None,
        list_only: bool = False,
        section: Optional[str] = None,
    ):
        self.pdf_source = pdf_source
        self.output_path = Path(output_path)
        self.dpi = dpi
        self.timeout = timeout
        self.figure_num = figure_num
        self.page = page
        self.list_only = list_only
        self.section = section

        # Determine if source is arXiv ID or file path
        self._is_arxiv = self._is_arxiv_id(pdf_source)
        if self._is_arxiv:
            self.arxiv_id = self._normalize_arxiv_id(pdf_source)
        else:
            self.arxiv_id = None

        # Create output directory if needed
        if not list_only:
            self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def _is_arxiv_id(self, source: str) -> bool:
        """Check if source is arXiv ID."""
        if '/' in source or source.endswith('.pdf'):
            return False
        return bool(re.match(r'^\d{4}\.\d{4,5}(v\d+)?$', source))

    def _normalize_arxiv_id(self, arxiv_id: str) -> str:
        """Normalize arXiv ID."""
        arxiv_id = re.sub(r'^arxiv:', '', arxiv_id, flags=re.IGNORECASE)
        arxiv_id = re.sub(r'\.pdf$', '', arxiv_id, flags=re.IGNORECASE)
        return arxiv_id.strip()

    def _get_pdf_url(self) -> str:
        """Build the PDF download URL."""
        return self.ARXIV_PDF_URL.format(arxiv_id=self.arxiv_id)

    def _get_pdf_content(self) -> bytes:
        """Get PDF content (download or read from file)."""
        if self._is_arxiv:
            url = self._get_pdf_url()
            print(f"⬇️  Downloading: {url}")
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
            }
            response = requests.get(url, headers=headers, timeout=self.timeout)
            response.raise_for_status()
            return response.content
        else:
            print(f"📂 Reading local PDF: {self.pdf_source}")
            with open(self.pdf_source, 'rb') as f:
                return f.read()

    def _find_figures(self, doc: fitz.Document) -> List[Dict]:
        """
        Find all figures in the PDF.

        Returns:
            List of dicts with keys: figure_num, page_num, caption, rects
        """
        figures = []
        figure_counter = 0

        for page_num, page in enumerate(doc):
            text = page.get_text()
            blocks = page.get_text("blocks")

            # Find figure captions
            for pattern in self.FIGURE_PATTERNS:
                matches = re.finditer(pattern, text, re.MULTILINE | re.IGNORECASE)
                for match in matches:
                    figure_counter += 1
                    caption = match.group(0)

                    # Get bounding box of the caption
                    # Find the block containing this caption
                    caption_rect = None
                    for block in blocks:
                        if caption in block[4]:
                            caption_rect = fitz.Rect(block[:4])
                            break

                    # Try to find images on this page
                    images = page.get_images()

                    # Estimate figure region (below caption, or image region)
                    page_width = page.rect.width
                    page_height = page.rect.height

                    if caption_rect:
                        # Figure is below the caption
                        figure_top = caption_rect.y1
                        figure_bottom = page_height
                        # Check next blocks for boundaries
                        for block in blocks:
                            if caption in block[4]:
                                continue
                            block_top = block[1]
                            if block_top > caption_rect.y1 and block_top < caption_rect.y1 + 200:
                                figure_bottom = block_top

                        figure_rect = fitz.Rect(
                            caption_rect.x0,  # left
                            figure_top,        # top
                            page_width - 50,   # right
                            figure_bottom      # bottom
                        )
                    else:
                        # Use image bounding boxes
                        figure_rect = None

                    figures.append({
                        'figure_num': figure_counter,
                        'page_num': page_num + 1,
                        'caption': caption,
                        'rect': figure_rect,
                        'has_images': len(images) > 0,
                        'image_count': len(images)
                    })

        return figures

    def _find_experiment_pages(self, doc: fitz.Document) -> List[int]:
        """Find pages that contain experiment/results sections."""
        experiment_pages = []

        for page_num, page in enumerate(doc):
            text = page.get_text()

            for pattern in self.EXPERIMENT_SECTIONS:
                if re.search(pattern, text, re.MULTILINE | re.IGNORECASE):
                    experiment_pages.append(page_num)
                    break

        return experiment_pages

    def _capture_figure_image(self, page: fitz.Page, figure_info: Dict, doc: fitz.Document = None) -> Optional[fitz.Pixmap]:
        """Capture the actual figure image from the page.

        Strategy:
        1. Try to extract embedded images first
        2. If that fails, capture the region containing the figure
        """
        images = page.get_images()

        # Try to get significant images
        for img_index, img in enumerate(images):
            try:
                xref = img[0]
                pix = fitz.Pixmap(doc, xref)

                # Skip very small images (icons, etc.)
                if pix.width < 200 or pix.height < 200:
                    pix = None
                    continue

                # Found a good image
                return pix
            except Exception:
                continue

        # Fallback: capture the region below caption (entire bottom half of page)
        if figure_info.get('rect'):
            zoom = self.dpi / 72
            matrix = fitz.Matrix(zoom, zoom)
            clip = figure_info['rect'] * zoom
            return page.get_pixmap(matrix=matrix, clip=clip)

        # Last resort: capture bottom half of the page
        page_height = page.rect.height
        page_width = page.rect.width
        zoom = self.dpi / 72
        matrix = fitz.Matrix(zoom, zoom)
        clip = fitz.Rect(0, page_height * 0.3, page_width, page_height)
        return page.get_pixmap(matrix=matrix, clip=clip)

    def _capture_page_figures(self, pdf_content: bytes, page_num: int, output_dir: Path) -> List[str]:
        """Capture all figures from a specific page."""
        saved_files = []

        with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
            tmp.write(pdf_content)
            tmp_path = tmp.name

        try:
            doc = fitz.open(tmp_path)
            page = doc[page_num]
            images = page.get_images()

            zoom = self.dpi / 72
            matrix = fitz.Matrix(zoom, zoom)

            # Get significant images (not icons)
            significant_images = []
            for img_index, img in enumerate(images):
                try:
                    xref = img[0]
                    pix = fitz.Pixmap(doc, xref)
                    if pix.width >= 200 and pix.height >= 200:
                        significant_images.append((img_index, pix))
                except Exception:
                    continue

            # Save up to 3 figures from this page
            for idx, pix in significant_images[:3]:
                output_path = output_dir / f"figure_{page_num + 1}_{idx + 1}.jpg"
                pix.save(str(output_path), output='jpeg')
                print(f"Saved: {output_path}")
                saved_files.append(str(output_path))
                pix = None  # Prevent double-free

            doc.close()

        finally:
            os.unlink(tmp_path)

        return saved_files

    def run(self) -> None:
        """Execute the capture process."""
        try:
            pdf_content = self._get_pdf_content()

            with tempfile.NamedTemporaryFile(suffix='.pdf', delete=False) as tmp:
                tmp.write(pdf_content)
                tmp_path = tmp.name

            try:
                doc = fitz.open(tmp_path)

                if self.list_only:
                    # Just list all figures
                    figures = self._find_figures(doc)
                    print(f"\n📊 Found {len(figures)} figures in the paper:\n")
                    for f in figures:
                        print(f"  Figure {f['figure_num']}: Page {f['page_num']} - {f['caption']}")
                        if f['has_images']:
                            print(f"    -> Contains {f['image_count']} image(s)")
                    return

                # Find figures
                figures = self._find_figures(doc)

                if self.page:
                    # Capture figures from specific page
                    page_num = self.page - 1  # Convert to 0-indexed
                    if page_num < 0 or page_num >= doc.page_count:
                        print(f"❌ Error: Page {self.page} not found")
                        sys.exit(1)

                    print(f"\n📊 Capturing figures from page {self.page}...")
                    saved = self._capture_page_figures(
                        pdf_content, page_num, self.output_path.parent
                    )

                    # Rename to expected output path if single figure
                    if len(saved) == 1:
                        os.rename(saved[0], str(self.output_path))
                        print(f"✅ Success! Captured to: {self.output_path}")
                    elif len(saved) > 1:
                        print(f"✅ Success! Captured {len(saved)} figures to: {self.output_path.parent}")
                    else:
                        print("❌ No figures found on this page")
                        sys.exit(1)

                elif self.figure_num:
                    # Capture specific figure
                    target_figure = next(
                        (f for f in figures if f['figure_num'] == self.figure_num),
                        None
                    )

                    if not target_figure:
                        print(f"❌ Error: Figure {self.figure_num} not found")
                        sys.exit(1)

                    print(f"\n📊 Capturing Figure {self.figure_num} from page {target_figure['page_num']}...")

                    page_num = target_figure['page_num'] - 1
                    page = doc[page_num]

                    # Try to capture image (pass doc for image extraction)
                    pix = self._capture_figure_image(page, target_figure, doc)

                    if pix:
                        # Use PNG format for reliability
                        output_path = self.output_path
                        if output_path.suffix.lower() not in ['.png', '.jpg', '.jpeg']:
                            output_path = output_path.with_suffix('.png')
                        pix.save(str(output_path))
                        print(f"✅ Success! Captured Figure {self.figure_num} to: {output_path}")
                    else:
                        print("❌ Could not capture figure")
                        sys.exit(1)

                elif self.section:
                    # Find experiment section pages
                    exp_pages = self._find_experiment_pages(doc)
                    if not exp_pages:
                        print("❌ Could not find Experiment/Results section")
                        sys.exit(1)

                    print(f"\n📊 Capturing figures from experiment section (pages {exp_pages[0] + 1}-{exp_pages[-1] + 1})...")

                    all_saved = []
                    for page_num in exp_pages[:3]:  # Limit to 3 pages
                        saved = self._capture_page_figures(
                            pdf_content, page_num, self.output_path.parent
                        )
                        all_saved.extend(saved)

                    if all_saved:
                        print(f"✅ Success! Captured {len(all_saved)} figures")
                    else:
                        print("❌ No figures found in experiment section")
                        sys.exit(1)

                else:
                    # Default: capture first experiment figure
                    if figures:
                        target_figure = figures[0]
                        print(f"\n📊 Capturing first figure: {target_figure['caption']}")
                        self.figure_num = target_figure['figure_num']
                        page = doc[target_figure['page_num'] - 1]
                        pix = self._capture_figure_image(page, target_figure)

                        if pix:
                            pix.save(str(self.output_path))
                            print(f"✅ Success! Captured to: {self.output_path}")
                        else:
                            print("❌ Could not capture figure")
                            sys.exit(1)
                    else:
                        print("❌ No figures found")
                        sys.exit(1)

                doc.close()

            finally:
                os.unlink(tmp_path)

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
            import traceback
            traceback.print_exc()
            sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='Capture figures from arXiv PDF as image(s)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument('pdf_source', help='arXiv ID (e.g., 1706.03762) or PDF file path')
    parser.add_argument('output_path', nargs='?', help='Output image path (e.g., images/001_figure.jpg)')

    parser.add_argument('--dpi', '-r', type=int, default=150,
                        help='Resolution in DPI (default: 150)')
    parser.add_argument('--timeout', '-t', type=int, default=60,
                        help='Download timeout in seconds (default: 60)')

    # Capture options (mutually exclusive)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--list', '-l', action='store_true',
                       help='List all figures in the paper')
    group.add_argument('--figure', '-f', type=int,
                       help='Capture specific figure number')
    group.add_argument('--page', '-p', type=int,
                       help='Capture all figures from specific page')
    group.add_argument('--section', '-s', type=str,
                       help='Capture figures from section (e.g., "Experiment", "Results")')

    args = parser.parse_args()

    if args.list:
        if not args.output_path:
            args.output_path = "figures_output"
    elif not args.output_path:
        parser.error("output_path is required when not using --list")

    capture = PDFFigureCapture(
        pdf_source=args.pdf_source,
        output_path=args.output_path or "output.jpg",
        dpi=args.dpi,
        timeout=args.timeout,
        figure_num=args.figure,
        page=args.page,
        list_only=args.list,
        section=args.section,
    )

    capture.run()


if __name__ == '__main__':
    main()
