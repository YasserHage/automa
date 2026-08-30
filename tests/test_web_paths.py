"""Every path the web builds is anchored and validated at one place.

`store.CATALOG_DIR` is `Path("catalog")` - relative to the process's working
directory, with no environment override - so a server started from anywhere else
would silently read and write the wrong catalog. These tests pin the two properties
that stop that: everything resolves against `config.REPO_ROOT`, and a name that
could escape a directory is refused before it builds a path at all.
"""

from __future__ import annotations

import pytest

from automa import config
from automa.web import paths


@pytest.mark.parametrize(
    "name",
    ["../secrets", "..", "a/b", "a\\b", "sushigo-old", "", "with space", "nim.py"],
)
def test_a_name_that_is_not_an_identifier_never_becomes_a_path(name: str) -> None:
    with pytest.raises(paths.InvalidNameError):
        paths.game_name(name)
    with pytest.raises(paths.InvalidNameError):
        paths.catalog_path(name)


def test_a_real_game_name_resolves_under_the_repo_root_not_the_cwd() -> None:
    path = paths.catalog_path("nim")
    assert path == config.REPO_ROOT / "catalog" / "nim.json"
    assert path.is_absolute()


def test_the_store_default_is_the_trap_this_module_exists_to_avoid() -> None:
    """Documented in CLAUDE.md; asserted here so a later 'simplification' is caught."""
    from automa.catalog import store

    assert not store.CATALOG_DIR.is_absolute(), (
        "if this becomes absolute the web's explicit path= is redundant, "
        "but never assign to it - it is process-global and a test reads it directly"
    )


@pytest.mark.parametrize(
    "filename",
    ["../../etc/passwd", "..\\win.ini", ".hidden.pdf", "", "a" * 200 + ".pdf"],
)
def test_an_unsafe_upload_filename_is_refused(filename: str) -> None:
    with pytest.raises(paths.InvalidNameError):
        paths.safe_upload_filename(filename)


@pytest.mark.parametrize("filename", ["rules.exe", "rules.zip", "rules.docx", "rules"])
def test_only_suffixes_the_ingester_can_read_are_accepted(filename: str) -> None:
    with pytest.raises(paths.InvalidNameError):
        paths.safe_upload_filename(filename)


@pytest.mark.parametrize("filename", ["rules.pdf", "cards.md", "notes.txt", "R-2.markdown"])
def test_real_rule_material_is_accepted(filename: str) -> None:
    assert paths.safe_upload_filename(filename) == filename


@pytest.mark.parametrize(
    ("sent", "kept"),
    [
        ("some/dir/rules.pdf", "rules.pdf"),
        ("/absolute.pdf", "absolute.pdf"),
    ],
)
def test_a_client_supplied_directory_is_stripped_rather_than_honoured(
    sent: str, kept: str
) -> None:
    """A browser can send a path in a multipart filename; only the basename is used.

    Stripping rather than refusing is deliberate: some browsers legitimately send a
    full path, and the file is still written into the job's own upload directory.
    """
    assert paths.safe_upload_filename(sent) == kept


@pytest.mark.parametrize("value", ["a/b", "a\\b", "../x", "", "   "])
def test_a_card_type_cannot_escape_its_directory(value: str) -> None:
    with pytest.raises(paths.InvalidNameError):
        paths.card_type(value)


def test_a_card_type_need_not_be_an_identifier() -> None:
    """A compiler may name one `salmon nigiri`; that is a card, not a module."""
    assert paths.card_type("salmon nigiri") == "salmon nigiri"
    assert paths.card_type("maki_2") == "maki_2"


def test_uploads_and_staging_stay_inside_the_repo() -> None:
    assert paths.upload_dir("abc123").is_relative_to(config.REPO_ROOT / "files")
    assert paths.staging_path("nim").is_relative_to(config.REPO_ROOT / "build")
    assert paths.install_path("nim").is_relative_to(config.REPO_ROOT / "src")


def test_uploads_land_under_the_gitignored_files_directory() -> None:
    """Invariant 6: no commercial rulebook or card text in the repository.

    `files/` is gitignored in full, which is what keeps an uploaded rulebook out of
    the repo without any `.gitignore` change - so the upload directory must stay
    inside it.
    """
    assert paths.UPLOADS_DIR.is_relative_to(config.REPO_ROOT / "files")
