"""Turning submitted rule material into something a model can read.

The Compiler's first problem is not understanding rules, it is *getting at* them. Real
rulebooks are frequently image-only scans with no text layer: the DNUP rulebook this
project was built against returns two bytes from ``pdftotext``, and rendering it needs
poppler, which is not installed on the development machine and cannot be assumed on a
judge's. So the pages are recovered directly from the PDF's own object stream using
nothing but the standard library.

Phase 3 is the first caller - it needs the pages to generate the baseline's rules
brief - and Phase 4 extends this into full rule ingestion. It lives here rather than in
``eval/`` because ingestion is the Compiler's job; Phase 3 is only borrowing it.

Scope is deliberately narrow: JPEG (``/DCTDecode``) images, which is what a scanned
page almost always is. Anything else is reported rather than silently skipped, because
a Compiler that quietly ingested half a rulebook would be worse than one that refused.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: PDF image objects, matched on the raw bytes. Parsing the object graph properly
#: would need a PDF library; for recovering page scans this is enough, and it keeps
#: the dependency list at zero.
_OBJECT = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.S)
_WIDTH = re.compile(rb"/Width\s+(\d+)")
_HEIGHT = re.compile(rb"/Height\s+(\d+)")


@dataclass(frozen=True)
class PageImage:
    """One extracted image, ready to send to a model."""

    object_id: int
    data: bytes
    mime_type: str
    width: int | None = None
    height: int | None = None

    def describe(self) -> str:
        size = f"{self.width}x{self.height}" if self.width else "unknown size"
        return f"obj {self.object_id}: {size} {self.mime_type} ({len(self.data):,} bytes)"


class IngestError(RuntimeError):
    """The material could not be read at all."""


def pdf_page_images(path: Path | str) -> list[PageImage]:
    """Every JPEG page image in a PDF, in document order.

    Raises:
        IngestError: The file holds no extractable image. That includes an ordinary
            text PDF, which needs a different path entirely - failing loudly here is
            better than handing the Compiler an empty list it would read as "no rules".
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise IngestError(f"cannot read {path}: {exc}") from exc

    pages: list[PageImage] = []
    other_filters: set[str] = set()

    for match in _OBJECT.finditer(data):
        body = match.group(3)
        if b"/Image" not in body:
            continue
        stream_at = body.find(b"stream")
        if stream_at == -1:
            continue

        header = body[:stream_at]
        if b"/DCTDecode" not in header:
            for name in (b"/FlateDecode", b"/JPXDecode", b"/CCITTFaxDecode"):
                if name in header:
                    other_filters.add(name.decode())
            continue

        start = body.find(b"\n", stream_at) + 1
        end = body.rfind(b"endstream")
        width = _WIDTH.search(header)
        height = _HEIGHT.search(header)
        pages.append(
            PageImage(
                object_id=int(match.group(1)),
                data=body[start:end].rstrip(b"\r\n"),
                mime_type="image/jpeg",
                width=int(width.group(1)) if width else None,
                height=int(height.group(1)) if height else None,
            )
        )

    if not pages:
        extra = f" Found non-JPEG image filters: {sorted(other_filters)}." if other_filters else ""
        raise IngestError(
            f"No JPEG page images found in {path}. This extractor handles scanned "
            f"(/DCTDecode) pages only.{extra}"
        )
    return pages


def as_message_parts(pages: list[PageImage], text: str) -> list[dict]:
    """A prompt of instructions followed by page images.

    The shape `call_llm` accepts, so rule material reaches a model through the one
    wrapper that logs it rather than around it (invariant 3).
    """
    return [
        {"type": "text", "text": text},
        *({"type": "image", "mime_type": p.mime_type, "data": p.data} for p in pages),
    ]
