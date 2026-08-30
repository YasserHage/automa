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

Real rule material comes in both shapes, and the difference is not cosmetic. The DNUP
rulebook is an image-only scan: ``pdftotext`` returns two bytes and the pages have to be
recovered as JPEGs. The Sushi Go rulebook is the opposite - 21KB of extractable text,
with several hundred *card illustrations* embedded in it. Handing the image extractor to
Sushi Go would send a compiler hundreds of thumbnails and no rules at all.

So :func:`read_material` looks first and decides: text where there is text, page images
where there is not. Getting that wrong is silent and expensive, which is why it is a
decision the code makes explicitly rather than something a caller has to know.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

#: Suffixes read as text directly, without going near a PDF parser. Rules pasted into a
#: text file are rule material as much as a scanned manual is.
TEXT_SUFFIXES: frozenset[str] = frozenset({".txt", ".md", ".markdown", ".text"})

#: Below this, a "text layer" is page furniture - a title, a page number, an artefact of
#: how the scan was produced - not rules anyone could compile from. DNUP's image-only
#: scan yields 2 characters; Sushi Go yields ~21,000. Nothing real sits near the line.
MIN_TEXT_CHARS = 500

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


def pdf_text(path: Path | str) -> str:
    """The text layer of a PDF, or an empty string if it has none.

    Uses `pypdf` rather than shelling out to `pdftotext`: poppler happens to be
    installed on this machine and would not be on a judge's, and a pinned library is
    the reproducible answer for something the reproduction guide depends on.
    """
    from pypdf import PdfReader

    path = Path(path)
    try:
        reader = PdfReader(str(path))
    except Exception as exc:  # noqa: BLE001 - a malformed PDF is the caller's problem
        raise IngestError(f"cannot read {path} as a PDF: {exc}") from exc
    pages = [(page.extract_text() or "") for page in reader.pages]
    return "\n\n".join(pages).strip()


@dataclass(frozen=True)
class Material:
    """Submitted rule material, in whichever form it actually exists."""

    source: str
    text: str = ""
    pages: list[PageImage] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.pages is None:
            object.__setattr__(self, "pages", [])

    @property
    def kind(self) -> str:
        return "text" if self.text else "images"

    def describe(self) -> str:
        if self.text:
            return f"{self.source}: text layer, {len(self.text):,} characters"
        return f"{self.source}: scanned, {len(self.pages)} page image(s)"


def read_material(path: Path | str) -> Material:
    """Read rule material, choosing the extraction that actually applies.

    Text where the PDF has a text layer; page images where it does not. The choice is
    made here rather than by the caller because getting it wrong fails *silently* - a
    compiler handed 500 card thumbnails instead of rules produces a confidently wrong
    module, not an error.

    Raises:
        IngestError: Neither path yields anything usable.
    """
    path = Path(path)

    # Plain text, taken at face value. A person with rules in a text file, a markdown
    # export or something they pasted out of a forum post has submitted rule material
    # just as much as someone with a scanned manual, and pushing it through a PDF
    # reader would only fail with a confusing error about a missing header.
    if path.suffix.lower() in TEXT_SUFFIXES:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise IngestError(f"cannot read {path} as text: {exc}") from exc
        if not text.strip():
            raise IngestError(f"{path} is empty")
        return Material(source=str(path), text=text)

    text = pdf_text(path)
    if len(text) >= MIN_TEXT_CHARS:
        return Material(source=str(path), text=text)

    try:
        pages = pdf_page_images(path)
    except IngestError as exc:
        raise IngestError(
            f"{path} has no usable text layer ({len(text)} characters) and no "
            f"extractable page images. {exc}"
        ) from exc
    return Material(source=str(path), pages=pages)
