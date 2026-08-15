"""Best-effort text extraction from uploaded document bytes.

Used by ``app.api.uploads`` so every upload type that is supposed to
contribute text to the session's combined transcript
(``SessionService.get_combined_transcript_text``) actually does so
correctly:

- Previously, ``transcript`` uploads were always UTF-8-decoded raw bytes -
  fine for plain-text files, but garbage (mojibake) for a PDF.
- ``supporting_document`` uploads never had ``transcript_text`` populated
  at all, so a genuinely important PDF (a requirements doc, a statement of
  work, etc.) silently contributed nothing to what the orchestrator/
  requirements-analyst agent ever saw - it was uploaded but never read.

This module fixes both: PDFs (detected by content type or file extension)
are parsed page-by-page via ``pypdf``; everything else keeps the original,
simple UTF-8 decode behavior.

Markdown (``.md``) transcripts/requirements documents are an explicitly
supported use case: they are plain UTF-8 text, so they fall through to the
same simple decode path as ``.txt`` - the raw Markdown (headings, lists,
etc.) is passed straight through as transcript text, which downstream
agents read just fine. No Markdown-specific parsing/stripping is done or
needed. Browsers frequently send an empty/generic ``content_type`` for
``.md`` file parts (no universally recognized ``text/markdown`` MIME type),
so detection here never depends on ``.md`` being tagged with any specific
content type - only the PDF path is content-type/extension sensitive.
"""
from __future__ import annotations

import io

from pypdf import PdfReader
from pypdf.errors import PyPdfError

__all__ = ["DocumentTextExtractionError", "extract_text"]

_PDF_CONTENT_TYPES = {"application/pdf"}
_PDF_EXTENSIONS = (".pdf",)


class DocumentTextExtractionError(RuntimeError):
    """Raised when uploaded document bytes cannot be parsed into text."""


def _is_pdf(*, content_type: str, file_name: str) -> bool:
    return content_type.lower() in _PDF_CONTENT_TYPES or file_name.lower().endswith(_PDF_EXTENSIONS)


def _extract_pdf_text(*, content: bytes, file_name: str) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        page_texts = [page.extract_text() or "" for page in reader.pages]
    except PyPdfError as exc:
        raise DocumentTextExtractionError(f"Unable to parse PDF '{file_name}': {exc}") from exc

    text = "\n\n".join(page_text for page_text in page_texts if page_text.strip())
    if not text.strip():
        raise DocumentTextExtractionError(
            f"PDF '{file_name}' contained no extractable text "
            "(it may be a scanned/image-only PDF)."
        )
    return text


def extract_text(*, content: bytes, content_type: str, file_name: str) -> str:
    """Extracts plain text from uploaded document bytes.

    Raises ``DocumentTextExtractionError`` (fail closed - callers should
    mark the upload as ``failed`` rather than silently recording empty/
    garbage text) if a detected PDF cannot be parsed or contains no
    extractable text.
    """
    if _is_pdf(content_type=content_type, file_name=file_name):
        return _extract_pdf_text(content=content, file_name=file_name)
    return content.decode("utf-8", errors="replace")
