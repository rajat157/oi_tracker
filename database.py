"""Shim — real module lives at db/legacy.py"""
from db.legacy import *  # noqa: F401,F403
from db.legacy import get_connection, init_db  # noqa: F401
