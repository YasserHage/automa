"""Upload, compile, watch, approve, install.

`files/` is gitignored in full, which is what keeps invariant 6 (no commercial rule
text in the repository) intact for uploads without touching `.gitignore` at all.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, File, Header, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse

from automa.compiler import ingest
from automa.web.models import CompileApproveRequest, CompileRequest, UploadResponse
from automa.web.paths import (
    InvalidNameError,
    new_job_id,
    safe_upload_filename,
    upload_dir,
)
from automa.web.paths import game_name as validate_game_name
from automa.web.routes.deps import jobs
from automa.web.services.compiling import JobNotFoundError, JobRegistry, JobStateError
from automa.web.streaming import is_close

router = APIRouter(prefix="/api", tags=["compile"])

#: Rule material is a rulebook, not a dataset. Generous for a scanned manual, and far
#: below anything that would exhaust memory reading it in one go.
MAX_UPLOAD_BYTES = 32 * 1024 * 1024

_KEEPALIVE_S = 15.0


@router.post("/uploads", response_model=UploadResponse)
async def upload_material(file: UploadFile = File(...)) -> dict[str, Any]:
    """Store one piece of rule material and report what was actually read from it.

    `read_material` runs immediately so the person sees "text layer, 21,143
    characters" or "images, 12 pages" *before* paying for a compile - a scan sent
    down the text path would deliver no rules at all, and the compile would produce a
    confidently wrong module rather than an error.
    """
    try:
        name = safe_upload_filename(file.filename or "")
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(payload) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413, detail=f"rule material may be at most {MAX_UPLOAD_BYTES} bytes"
        )

    job_id = new_job_id()
    directory = upload_dir(job_id)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / name
    target.write_bytes(payload)

    try:
        material = ingest.read_material(target)
    except ingest.IngestError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {
        "job_id": job_id,
        "filename": name,
        "kind": material.kind,
        "detail": material.describe(),
    }


def _uploaded(material_id: str) -> Any:
    directory = upload_dir(material_id)
    if not directory.is_dir():
        raise HTTPException(status_code=404, detail=f"no upload {material_id!r}")
    files = sorted(p for p in directory.iterdir() if p.is_file())
    if not files:
        raise HTTPException(status_code=404, detail=f"upload {material_id!r} is empty")
    return files[0]


@router.post("/compile")
def start_compile(
    body: CompileRequest, registry: JobRegistry = Depends(jobs)
) -> dict[str, Any]:
    try:
        game = validate_game_name(body.game)
    except InvalidNameError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    material = _uploaded(body.material_id)
    cards = _uploaded(body.cards_id) if body.cards_id else None
    job = registry.start(
        game=game,
        material=material,
        cards=cards,
        use_catalog=body.use_catalog,
        attempts=body.attempts,
        matches=body.matches,
    )
    return job.snapshot()


@router.get("/compile/{job_id}")
def get_job(job_id: str, registry: JobRegistry = Depends(jobs)) -> dict[str, Any]:
    try:
        return registry.get(job_id).snapshot()
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no compile job {job_id!r}") from exc


@router.get("/compile/{job_id}/events")
async def job_events(
    job_id: str,
    request: Request,
    registry: JobRegistry = Depends(jobs),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
) -> StreamingResponse:
    try:
        job = registry.get(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no compile job {job_id!r}") from exc
    try:
        after = int(last_event_id) if last_event_id else 0
    except ValueError:
        after = 0

    async def stream() -> AsyncIterator[str]:
        async with job.broadcaster.subscribe(after=after) as events:
            while True:
                if await request.is_disconnected():
                    return
                try:
                    item = await asyncio.wait_for(events.get(), timeout=_KEEPALIVE_S)
                except TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                if is_close(item):
                    return
                yield item.encode()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/compile/{job_id}/approve")
def approve(
    job_id: str, body: CompileApproveRequest, registry: JobRegistry = Depends(jobs)
) -> dict[str, Any]:
    """Install the compiled module. The one gate in the pipeline that is not automated.

    `acknowledged` is checked by the *server*, not merely disabled in the client, and
    there is deliberately no web equivalent of the CLI's `--yes`: the flag exists for
    scripted runs, the web has no scripted-run case, and installing without review
    "has thrown away the property the whole architecture is built on".
    """
    if body.acknowledged is not True:
        raise HTTPException(status_code=400, detail="the review must be acknowledged")
    try:
        return registry.approve(job_id, overwrite=body.overwrite)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no compile job {job_id!r}") from exc
    except JobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/compile/{job_id}/reject")
def reject(job_id: str, registry: JobRegistry = Depends(jobs)) -> dict[str, Any]:
    try:
        return registry.reject(job_id)
    except JobNotFoundError as exc:
        raise HTTPException(status_code=404, detail=f"no compile job {job_id!r}") from exc
    except JobStateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
