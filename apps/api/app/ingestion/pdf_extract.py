"""PDF text extraction (BUILD_FLOW Phase 2).

- Digital pages: PyMuPDF (fitz) text extraction.
- Scanned pages (no extractable text): OCR path via injected ``ocr_fn``.
- Per-page detection: a page is 'scanned' when it yields no text.
- ``fitz`` is imported lazily so the API boots without the dependency.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PageText:
    page_no: int  # 1-based
    text: str
    scanned: bool


def extract_pdf_text(
    pdf_bytes: bytes,
    ocr_fn=None,
    ocr_dpi: int = 200,
) -> tuple[str, int, list[PageText], bool]:
    """Extract text from PDF bytes.

    Returns (full_text, page_count, pages, used_ocr).
    Raises RuntimeError on invalid PDFs, or when a scanned page is found
    and no ``ocr_fn`` is configured.
    """
    try:
        import fitz  # PyMuPDF; lazy import
    except ImportError as e:
        raise RuntimeError("PyMuPDF (fitz) is required for PDF extraction") from e

    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as e:
        raise RuntimeError(f"invalid or unreadable PDF: {e}") from e

    pages: list[PageText] = []
    used_ocr = False
    with doc:
        for i, page in enumerate(doc):
            text = (page.get_text("text") or "").strip()
            if text:
                pages.append(PageText(page_no=i + 1, text=text, scanned=False))
            else:
                used_ocr = True
                if ocr_fn is None:
                    raise RuntimeError(f"page {i + 1} appears scanned (no extractable text) and no OCR is configured")
                pix = page.get_pixmap(dpi=ocr_dpi)
                ocr_text = (ocr_fn(pix.tobytes("png"), i + 1) or "").strip()
                pages.append(PageText(page_no=i + 1, text=ocr_text, scanned=True))

    full_text = "\n\n".join(p.text for p in pages if p.text)
    return full_text, len(pages), pages, used_ocr


def default_ocr_fn(image_png: bytes, page_no: int) -> str:
    """OCR via pytesseract/tesseract when installed. Raises otherwise."""
    try:
        from PIL import Image  # noqa: F401
        import pytesseract
    except ImportError as e:
        raise RuntimeError("OCR requested but pytesseract/Pillow is not installed") from e
    import io

    from PIL import Image

    img = Image.open(io.BytesIO(image_png))
    return pytesseract.image_to_string(img)
