"""Every filesystem path the web layer touches, anchored to the repo root.

``catalog.store.CATALOG_DIR`` is ``Path("catalog")``, relative to the process's
working directory, with no environment override. A server started from anywhere
other than the repo root would silently read and write the wrong catalog - so every
path here is built from :data:`automa.config.REPO_ROOT` instead, and every caller
that touches the store passes one of these explicitly rather than letting it fall
back to its own relative default.

Path traversal is closed at the one place names turn into paths: a game name or a
card type that is not a valid Python identifier is rejected here, before it is used
to build anything, rather than trusted because some caller happened to check first.
"""

from __future__ import annotations

import re
import uuid
from pathlib import Path

from automa import config

CATALOG_DIR = config.REPO_ROOT / "catalog"
UPLOADS_DIR = config.REPO_ROOT / "files" / "uploads"
BUILD_DIR = config.REPO_ROOT / "build"
GAMES_DIR = config.REPO_ROOT / "src" / "automa" / "games"

#: Suffixes `compiler.ingest.read_material` can actually read.
ALLOWED_UPLOAD_SUFFIXES: frozenset[str] = frozenset(
    {".pdf", ".txt", ".md", ".markdown", ".text"}
)

#: Refuses anything that is not a plain filename component: no separators, no `..`,
#: no leading dot, nothing but the characters a Windows and POSIX filesystem both
#: accept without ceremony.
_SAFE_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class InvalidNameError(ValueError):
    """A name from a request cannot be used to build a path."""


def game_name(name: str) -> str:
    """Validate a game name used to build a path. Returns it unchanged if sound.

    A game is loaded as ``automa.games.<name>``, so anything that is not a valid
    Python identifier can neither name a real module nor be smuggled into a path -
    checked once, here, rather than separately in every caller that builds one.
    """
    if not isinstance(name, str) or not name.isidentifier():
        raise InvalidNameError(f"{name!r} is not a valid game name")
    return name


def card_type(value: str) -> str:
    """Validate a card type used to build a path segment.

    Card types are not required to be Python identifiers (a compiler may name one
    ``salmon nigiri``), but they still must not be able to escape the directory they
    are written into.
    """
    if not isinstance(value, str) or not value.strip():
        raise InvalidNameError("card type must be a non-empty string")
    if "/" in value or "\\" in value or ".." in value:
        raise InvalidNameError(f"{value!r} is not a valid card type")
    return value


def catalog_path(game: str) -> Path:
    """Where ``game``'s catalog lives, always under :data:`CATALOG_DIR`."""
    return CATALOG_DIR / f"{game_name(game)}.json"


def safe_upload_filename(name: str) -> str:
    """The basename of an uploaded file, stripped of any path and checked.

    Only the basename is kept - a client-supplied path is not honoured at all - and
    what remains must match a conservative allowlist and end in a suffix
    :mod:`automa.compiler.ingest` can read.
    """
    base = Path(name).name
    if not _SAFE_FILENAME.match(base):
        raise InvalidNameError(f"{name!r} is not a safe upload filename")
    suffix = Path(base).suffix.lower()
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        raise InvalidNameError(
            f"{suffix!r} is not a supported rule-material suffix "
            f"(allowed: {sorted(ALLOWED_UPLOAD_SUFFIXES)})"
        )
    return base


def new_job_id() -> str:
    return uuid.uuid4().hex[:12]


def upload_dir(job_id: str) -> Path:
    return UPLOADS_DIR / job_id


def staging_path(game: str) -> Path:
    return BUILD_DIR / f"{game_name(game)}_compiled.py"


def install_path(installed_name: str) -> Path:
    return GAMES_DIR / f"{game_name(installed_name)}.py"
