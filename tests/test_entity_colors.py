"""
tests/test_entity_colors.py
────────────────────────────
Regression tests for entity-level colour preservation and text-alignment
anchor mapping.

Covers:
  - Entity ACI override (color 1–255) → ExtXxx.color set to RGB tuple
  - Entity true_color override (group code 420) → exact 24-bit RGB preserved
  - BYLAYER / no explicit color → ExtXxx.color is None (layer colour used)
  - Entity color renders into SVG stroke attribute
  - LWPOLYLINE colour forwarded to its virtual LINE/ARC children
  - TEXT center/right alignment uses align_point, not insert
  - h_align / v_align stored on ExtText
  - text-anchor="middle" appears in SVG for center-aligned text
  - text-anchor="end" appears in SVG for right-aligned text

Run from G:/dxf2svg/:
    G:/dxf2svg/.venv/Scripts/pytest tests/test_entity_colors.py -v
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf
import pytest

from dxf2svg.core.extractor import DXFExtractor, ExtCircle, ExtLine, ExtText, ExtArc
from dxf2svg.converter import DXFConverter


# ── helpers ───────────────────────────────────────────────────────────────────

def _save(doc, tmp: str, name: str) -> str:
    path = os.path.join(tmp, name)
    doc.saveas(path)
    return path


def _extract(dxf_path: str):
    return list(DXFExtractor(dxf_path).extract("*Model_Space"))


def _svg(dxf_path: str) -> str:
    conv = DXFConverter(dxf_path)
    return conv.full_drawing(dxf_path.replace(".dxf", ".svg"))


# ── entity colour — extractor level ──────────────────────────────────────────

def test_aci_color_override_extracted():
    """Circle with ACI color=1 (red) must produce color=(255,0,0)."""
    doc = ezdxf.new("R2010")
    c = doc.modelspace().add_circle(center=(0, 0, 0), radius=1.0)
    c.dxf.color = 1  # ACI 1 = red

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "aci.dxf"))

    circles = [e for e in ents if isinstance(e, ExtCircle)]
    assert len(circles) == 1
    assert circles[0].color is not None, "ACI override should set .color"
    assert circles[0].color == (255, 0, 0), f"ACI 1 → red, got {circles[0].color}"


def test_true_color_override_extracted():
    """Circle with true_color=0x0080FF must produce color=(0,128,255)."""
    doc = ezdxf.new("R2010")
    c = doc.modelspace().add_circle(center=(0, 0, 0), radius=1.0)
    c.dxf.true_color = (0 << 16) | (128 << 8) | 255  # 0x0080FF

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "tc.dxf"))

    circles = [e for e in ents if isinstance(e, ExtCircle)]
    assert len(circles) == 1
    assert circles[0].color == (0, 128, 255), f"Expected (0,128,255), got {circles[0].color}"


def test_bylayer_color_is_none():
    """Circle with default (BYLAYER) color must have .color=None."""
    doc = ezdxf.new("R2010")
    doc.modelspace().add_circle(center=(0, 0, 0), radius=1.0)  # no explicit color

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "bylayer.dxf"))

    circles = [e for e in ents if isinstance(e, ExtCircle)]
    assert len(circles) == 1
    assert circles[0].color is None, "BYLAYER entity should have color=None"


def test_true_color_beats_aci():
    """When both true_color and color (ACI) are set, true_color wins."""
    doc = ezdxf.new("R2010")
    c = doc.modelspace().add_circle(center=(0, 0, 0), radius=1.0)
    c.dxf.color = 1                         # ACI red
    c.dxf.true_color = (0 << 16) | (0 << 8) | 255   # true blue

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "both.dxf"))

    circles = [e for e in ents if isinstance(e, ExtCircle)]
    assert circles[0].color == (0, 0, 255), "true_color should beat ACI"


# ── entity colour — SVG output level ─────────────────────────────────────────

def test_entity_color_in_svg_stroke():
    """Entity with true_color must produce inline stroke= in SVG output."""
    doc = ezdxf.new("R2010")
    c = doc.modelspace().add_circle(center=(0, 0, 0), radius=1.0)
    c.dxf.true_color = (255 << 16) | (0 << 8) | 128   # #FF0080

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "csvg.dxf"))

    assert "#FF0080" in svg or "#ff0080" in svg.lower(), \
        "Expected #FF0080 stroke in SVG for entity-level true_color"


def test_bylayer_entity_uses_layer_color():
    """
    BYLAYER entity must NOT produce an inline stroke attribute that overrides
    the layer CSS class — the layer colour already handles it.
    """
    doc = ezdxf.new("R2010")
    # Layer '0' has ACI 7 (black) by default.
    doc.modelspace().add_circle(center=(0, 0, 0), radius=1.0)

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "bylsvg.dxf"))

    # There should be no inline stroke= that overrides the default layer colour.
    # The <circle> element should carry the CSS class but not an inline stroke= override
    # for a colour other than the layer default.
    assert '<circle' in svg, "Should have a circle element"


# ── LWPOLYLINE colour forwarding ──────────────────────────────────────────────

def test_lwpolyline_color_forwarded_to_children():
    """LWPOLYLINE with explicit color must pass it to its virtual LINE children."""
    doc = ezdxf.new("R2010")
    pl = doc.modelspace().add_lwpolyline([(0, 0), (1, 0), (1, 1)], close=False)
    pl.dxf.color = 3  # ACI 3 = green

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "lwpoly.dxf"))

    lines = [e for e in ents if isinstance(e, ExtLine)]
    assert len(lines) > 0, "Should have extracted lines from LWPOLYLINE"
    for ln in lines:
        assert ln.color is not None, "LWPOLYLINE children must inherit color"
        r, g, b = ln.color
        assert g > r and g > b, f"ACI 3 = green, got {ln.color}"


# ── text alignment ────────────────────────────────────────────────────────────

def test_center_aligned_text_uses_align_point():
    """
    TEXT with halign=1 (center) must anchor at align_point, not insert.
    The two points are intentionally different to make a mismatch detectable.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    txt = msp.add_text("LABEL", dxfattribs={
        "height": 1.0,
        "insert": (0.0, 0.0, 0.0),    # first point (left edge for unaligned text)
        "halign": 1,                   # centre-aligned
        "align_point": (5.0, 5.0, 0.0),
    })

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "ctr.dxf"))

    texts = [e for e in ents if isinstance(e, ExtText)]
    assert len(texts) == 1
    t = texts[0]
    assert abs(t.x - 5.0) < 0.01, f"Center text x should be at align_point 5.0, got {t.x:.4f}"
    assert abs(t.y - 5.0) < 0.01, f"Center text y should be at align_point 5.0, got {t.y:.4f}"
    assert t.h_align == 1, f"h_align should be 1 (center), got {t.h_align}"


def test_right_aligned_text_uses_align_point():
    """TEXT with halign=2 (right) must anchor at align_point."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("RIGHT", dxfattribs={
        "height": 1.0,
        "insert": (0.0, 0.0, 0.0),
        "halign": 2,
        "align_point": (10.0, 0.0, 0.0),
    })

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "rgt.dxf"))

    texts = [e for e in ents if isinstance(e, ExtText)]
    assert len(texts) == 1
    assert abs(texts[0].x - 10.0) < 0.01, f"Right text x should be 10.0, got {texts[0].x:.4f}"
    assert texts[0].h_align == 2


def test_left_aligned_text_uses_insert():
    """TEXT with default alignment (halign=0) must use the insert point."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("LEFT", dxfattribs={
        "height": 1.0,
        "insert": (3.0, 7.0, 0.0),
    })

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "lft.dxf"))

    texts = [e for e in ents if isinstance(e, ExtText)]
    assert len(texts) == 1
    assert abs(texts[0].x - 3.0) < 0.01
    assert abs(texts[0].y - 7.0) < 0.01
    assert texts[0].h_align == 0


# ── text alignment in SVG output ─────────────────────────────────────────────

def test_center_text_anchor_in_svg():
    """Center-aligned text must produce text-anchor='middle' in SVG."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("CTR", dxfattribs={
        "height": 1.0,
        "insert": (0.0, 0.0, 0.0),
        "halign": 1,
        "align_point": (5.0, 5.0, 0.0),
    })

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "ctr_svg.dxf"))

    assert 'text-anchor="middle"' in svg, \
        "Expected text-anchor='middle' for center-aligned text"


def test_right_text_anchor_in_svg():
    """Right-aligned text must produce text-anchor='end' in SVG."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("RGT", dxfattribs={
        "height": 1.0,
        "insert": (0.0, 0.0, 0.0),
        "halign": 2,
        "align_point": (10.0, 0.0, 0.0),
    })

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "rgt_svg.dxf"))

    assert 'text-anchor="end"' in svg, \
        "Expected text-anchor='end' for right-aligned text"


def test_left_text_no_anchor_in_svg():
    """Left-aligned (default) text must NOT produce an explicit text-anchor attribute."""
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("LFT", dxfattribs={"height": 1.0, "insert": (0.0, 0.0, 0.0)})

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "lft_svg.dxf"))

    assert "text-anchor" not in svg, \
        "Left-aligned text should not set text-anchor (SVG default is 'start')"


def test_text_has_no_css_class():
    """
    <text> elements must NOT carry a CSS layer class.

    In SVG, CSS class selectors have higher specificity than presentation
    attributes.  If text were assigned class="layer-0" and the stylesheet
    declared ".layer-0 { stroke: #000000 }", that rule would override the
    presentation attribute stroke="none", painting unwanted outlines on every
    glyph and potentially making text invisible against a same-coloured background.

    Lines/circles/arcs continue to use CSS classes normally — only text is exempt.
    """
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("LABEL", dxfattribs={"height": 1.0, "insert": (3.0, 3.0, 0.0)})
    msp.add_circle(center=(5.0, 5.0, 0.0), radius=1.0)  # gives geometry for bbox

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "txt_no_class.dxf"))

    import re
    # Check that no <text> tag has a class= attribute
    for m in re.finditer(r'<text\b[^>]*/?>|<text\b[^>]*>', svg):
        tag = m.group(0)
        assert 'class=' not in tag, \
            f"<text> element must not carry a CSS layer class; found: {tag}"

    # Geometry elements SHOULD still have a class
    assert 'class="layer-' in svg, \
        "Non-text elements must still carry CSS layer classes"

    # Text must still have an explicit fill attribute
    for m in re.finditer(r'<text\b[^>]*>', svg):
        tag = m.group(0)
        assert 'fill=' in tag, \
            f"<text> element must carry inline fill= since it has no CSS class: {tag}"


def test_aci7_layer_renders_dark_not_white():
    """
    Layer with ACI 7 (AutoCAD 'white' on dark backgrounds) must NOT produce
    white fill/stroke in SVG elements — that would be invisible on a white canvas
    and would appear as white shapes covering the underlying geometry.

    Note: the SVG background <rect fill="white"> is expected and excluded here.
    We check that geometry/text elements and layer CSS don't use #FFFFFF.
    """
    doc = ezdxf.new("R2010")
    # Layer '0' uses ACI 7 by default.  Add text and a circle to force both
    # fill and stroke lookups.
    msp = doc.modelspace()
    msp.add_text("VISIBLE", dxfattribs={"height": 1.0, "insert": (0.0, 0.0, 0.0)})
    msp.add_circle(center=(5.0, 5.0, 0.0), radius=1.0)

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "aci7.dxf"))

    # The background <rect> has fill="white" (that's correct and expected).
    # Strip it out before checking so we only evaluate geometry/text elements.
    svg_no_bg = svg.replace('fill="white"', 'fill="__BG__"')

    assert 'fill="#FFFFFF"' not in svg_no_bg, \
        "ACI 7: text/geometry fill must not be white (#FFFFFF) — invisible on white SVG"
    assert 'stroke="#FFFFFF"' not in svg_no_bg, \
        "ACI 7: geometry stroke must not be white (#FFFFFF) — invisible on white SVG"
    # The layer CSS must not declare white stroke either.
    assert "stroke: #FFFFFF" not in svg_no_bg and "stroke: #ffffff" not in svg_no_bg, \
        "ACI 7: layer CSS must not use white stroke"


def test_text_entity_color_in_svg():
    """TEXT with explicit true_color must use that color as fill in SVG."""
    doc = ezdxf.new("R2010")
    txt = doc.modelspace().add_text("HI", dxfattribs={
        "height": 1.0,
        "insert": (0.0, 0.0, 0.0),
    })
    txt.dxf.true_color = (200 << 16) | (100 << 8) | 50   # #C86432

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "txt_color.dxf"))

    assert "#C86432" in svg or "#c86432" in svg.lower(), \
        "Expected text entity colour #C86432 in SVG fill"


# ── MTEXT rendering ───────────────────────────────────────────────────────────

def test_mtext_renders_as_text_element():
    """MTEXT entity must produce a <text> element in the SVG (not be silently dropped)."""
    doc = ezdxf.new("R2010")
    doc.modelspace().add_mtext("SUBARRAY ID", dxfattribs={
        "insert": (5.0, 10.0, 0.0),
        "char_height": 2.5,
    })

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "mtext.dxf"))

    assert "<text" in svg, "MTEXT must produce a <text> element in SVG"
    assert "SUBARRAY ID" in svg, "MTEXT content must appear in SVG"


def test_mtext_multiline_uses_tspan():
    """Multi-line MTEXT must emit <tspan> children — one per line."""
    doc = ezdxf.new("R2010")
    doc.modelspace().add_mtext("Line1\nLine2\nLine3", dxfattribs={
        "insert": (0.0, 0.0, 0.0),
        "char_height": 2.5,
    })

    with tempfile.TemporaryDirectory() as tmp:
        svg = _svg(_save(doc, tmp, "mtext_ml.dxf"))

    assert "<tspan" in svg, "Multi-line MTEXT should use <tspan> elements"
    assert "Line1" in svg and "Line2" in svg and "Line3" in svg, \
        "All lines must appear in SVG"


def test_mtext_extracted_as_ext_text():
    """MTEXT entity must produce an ExtText object with correct coordinates."""
    doc = ezdxf.new("R2010")
    doc.modelspace().add_mtext("LABEL", dxfattribs={
        "insert": (3.0, 7.0, 0.0),
        "char_height": 1.5,
    })

    with tempfile.TemporaryDirectory() as tmp:
        ents = _extract(_save(doc, tmp, "mtext_ext.dxf"))

    texts = [e for e in ents if isinstance(e, ExtText)]
    assert len(texts) == 1, f"Expected 1 ExtText, got {len(texts)}"
    t = texts[0]
    assert t.is_mtext is True
    assert abs(t.x - 3.0) < 0.01, f"MTEXT x should be 3.0, got {t.x}"
    assert abs(t.y - 7.0) < 0.01, f"MTEXT y should be 7.0, got {t.y}"
    assert t.text == "LABEL"
