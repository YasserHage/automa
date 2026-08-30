"""Every domain call the web makes, as pure functions returning plain dicts.

Routes do HTTP only. That split is what lets the tests drive the whole feature
offline without binding a port, which the suite requires - `socket` is on the
sandbox's banned-import list and `tests/conftest.py` keeps the run credential-free.
"""
