"""Central path to the db package (single source of truth for sys.path hacks)."""

import os


def db_src() -> str:
    here = os.path.dirname(os.path.abspath(__file__))  # apps/api/app
    return os.path.normpath(os.path.join(here, "..", "..", "..", "packages", "db", "src"))


def ensure_db_path() -> str:
    path = db_src()
    if path not in __import__("sys").path:
        __import__("sys").path.insert(0, path)
    return path
