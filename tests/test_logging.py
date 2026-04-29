"""
tests/test_logging.py
─────────────────────
DX-FH-005: DXFConverter must not call logging.basicConfig at construction time.

A library that touches the root logger silently overrides whatever logging
configuration the embedding application (ELiGen) has already installed.  Each
symbol-render call instantiates a fresh DXFConverter, so a single-line
basicConfig in __init__ defeats ELiGen's own format/level for the rest of the
process.

Two assertions:
  1. converter.py source does not contain `logging.basicConfig` (anywhere).
  2. Constructing a DXFConverter does not add handlers to the root logger.
"""

import logging
import os
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT.parent))


def test_converter_source_does_not_call_basicConfig():
    """DX-FH-005: converter.py must not call logging.basicConfig."""
    src = (REPO_ROOT / "converter.py").read_text(encoding="utf-8")
    # Block any call form: logging.basicConfig(...) or logging .basicConfig(
    assert not re.search(r"\blogging\s*\.\s*basicConfig\s*\(", src), (
        "converter.py contains a logging.basicConfig() call — DX-FH-005 regression. "
        "Libraries must not configure the root logger; move the call to cli.py / server.py."
    )


def test_dxf_converter_init_does_not_add_root_handlers():
    """Constructing DXFConverter must not append handlers to the root logger.

    basicConfig() is a no-op once the root logger has handlers, but if a fresh
    process imports dxf2svg first, basicConfig would attach a default
    StreamHandler.  This test starts from a known-clean root logger.
    """
    from dxf2svg.converter import DXFConverter

    fixture = REPO_ROOT / "tests" / "fixtures" / "minimal.dxf"
    if not fixture.exists():
        pytest.skip("minimal.dxf fixture not available")

    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        # Reset root to the unconfigured state.
        root.handlers.clear()
        root.setLevel(logging.WARNING)

        DXFConverter(str(fixture))

        assert root.handlers == [], (
            "DXFConverter.__init__ added a handler to the root logger — "
            "DX-FH-005 regression."
        )
        assert root.level == logging.WARNING, (
            "DXFConverter.__init__ changed the root logger level — "
            "DX-FH-005 regression."
        )
    finally:
        root.handlers = saved_handlers
        root.setLevel(saved_level)
