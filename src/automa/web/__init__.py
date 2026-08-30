"""The web surface: a local, single-user UI over the same calls the CLI makes.

An **additional** surface. The CLI stays canonical - the reproduction guide, the eval
battery and every measured result run through it unchanged, and nothing in `automa.eval`
knows this package exists.
"""

from automa.web.app import create_app

__all__ = ["create_app"]
