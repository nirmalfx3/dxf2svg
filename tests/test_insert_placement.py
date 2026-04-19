"""
tests/test_insert_placement.py
──────────────────────────────
Verifies that INSERT entities correctly apply the block base_point when
computing world-space geometry positions.

Regression for: _build_insert_matrix() omitted translate(-base_point), causing
blocks with a non-zero base_point to render at the wrong world position.

Run from G:/dxf2svg/:
    G:/dxf2svg/.venv/Scripts/pytest tests/test_insert_placement.py -v
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import ezdxf
import pytest

from dxf2svg.core.extractor import DXFExtractor, ExtCircle, ExtArc


# ── helpers ───────────────────────────────────────────────────────────────────

def _save(doc, tmp: str, name: str) -> str:
    path = os.path.join(tmp, name)
    doc.saveas(path)
    return path


def _circles(dxf_path: str):
    ext = DXFExtractor(dxf_path)
    return [e for e in ext.extract("*Model_Space") if isinstance(e, ExtCircle)]


def _arcs(dxf_path: str):
    ext = DXFExtractor(dxf_path)
    return [e for e in ext.extract("*Model_Space") if isinstance(e, ExtArc)]


# ── tests ─────────────────────────────────────────────────────────────────────

def test_insert_applies_base_point():
    """
    Block 'CB': circle at (0.5, 0.5), base_point = (0.5, 0.5).
    INSERT 'CB' at (2.0, 3.0).

    Expected world circle centre: (2.0, 3.0)  — base_point aligns with INSERT.
    Without fix it would appear at: (2.5, 3.5).
    """
    doc = ezdxf.new("R2010")
    cb = doc.blocks.new("CB")
    cb.block.dxf.base_point = (0.5, 0.5, 0)
    cb.add_circle(center=(0.5, 0.5, 0), radius=0.25)
    doc.modelspace().add_blockref("CB", insert=(2.0, 3.0))

    with tempfile.TemporaryDirectory() as tmp:
        circles = _circles(_save(doc, tmp, "base_point.dxf"))

    assert len(circles) == 1, f"Expected 1 circle, got {len(circles)}"
    cx, cy = circles[0].cx, circles[0].cy
    assert abs(cx - 2.0) < 0.01, f"Circle X wrong: expected 2.0, got {cx:.4f}"
    assert abs(cy - 3.0) < 0.01, f"Circle Y wrong: expected 3.0, got {cy:.4f}"


def test_zero_base_point_unchanged():
    """
    Block with base_point = (0, 0) behaves as before: circle at block-local
    position (1, 1) inserted at (3, 4) → world (4, 5).
    """
    doc = ezdxf.new("R2010")
    blk = doc.blocks.new("DOT0")
    blk.block.dxf.base_point = (0, 0, 0)
    blk.add_circle(center=(1, 1, 0), radius=0.1)
    doc.modelspace().add_blockref("DOT0", insert=(3.0, 4.0))

    with tempfile.TemporaryDirectory() as tmp:
        circles = _circles(_save(doc, tmp, "zero_base.dxf"))

    assert len(circles) == 1
    assert abs(circles[0].cx - 4.0) < 0.01
    assert abs(circles[0].cy - 5.0) < 0.01


def test_same_block_two_inserts():
    """
    Same block inserted at two positions must yield two circles at distinct world
    coordinates (regression: cycle guard must not swallow the second insert).
    """
    doc = ezdxf.new("R2010")
    blk = doc.blocks.new("DOT2")
    blk.block.dxf.base_point = (0, 0, 0)
    blk.add_circle(center=(0, 0, 0), radius=0.1)
    msp = doc.modelspace()
    msp.add_blockref("DOT2", insert=(1.0, 0.0))
    msp.add_blockref("DOT2", insert=(4.0, 0.0))

    with tempfile.TemporaryDirectory() as tmp:
        circles = _circles(_save(doc, tmp, "two_inserts.dxf"))

    assert len(circles) == 2, f"Expected 2 circles, got {len(circles)}"
    xs = sorted(c.cx for c in circles)
    assert abs(xs[0] - 1.0) < 0.01, f"First circle X: expected 1.0, got {xs[0]:.4f}"
    assert abs(xs[1] - 4.0) < 0.01, f"Second circle X: expected 4.0, got {xs[1]:.4f}"


def test_nested_insert_base_point():
    """
    Nested INSERTs: model space inserts OUTER at (10, 10); OUTER inserts INNER
    at (2, 0) with INNER's base_point at (1, 0).  The circle at (1, 0) in INNER
    should land at world (10 + 2 + 1 - 1, 10) = (12, 10).
    """
    doc = ezdxf.new("R2010")
    inner = doc.blocks.new("INNER")
    inner.block.dxf.base_point = (1, 0, 0)
    inner.add_circle(center=(1, 0, 0), radius=0.2)

    outer = doc.blocks.new("OUTER")
    outer.block.dxf.base_point = (0, 0, 0)
    outer.add_blockref("INNER", insert=(2.0, 0.0))

    doc.modelspace().add_blockref("OUTER", insert=(10.0, 10.0))

    with tempfile.TemporaryDirectory() as tmp:
        circles = _circles(_save(doc, tmp, "nested.dxf"))

    assert len(circles) == 1, f"Expected 1 circle, got {len(circles)}"
    assert abs(circles[0].cx - 12.0) < 0.01, f"Nested X: expected 12.0, got {circles[0].cx:.4f}"
    assert abs(circles[0].cy - 10.0) < 0.01, f"Nested Y: expected 10.0, got {circles[0].cy:.4f}"


def test_nested_insert_with_rotation():
    """
    Regression for wrong matrix composition order (parent_m @ local_m was wrong;
    correct is local_m @ parent_m for ezdxf row-vector convention).

    Setup:
      Model space: INSERT 'OTR' at (10, 0) with rotation=90°
      Block 'OTR': INSERT 'INR' at (0, 3) with no rotation
      Block 'INR': circle at (0, 0)

    Transform chain for circle (0, 0):
      1. INNER INSERT translate(0, 3): (0, 0) → (0, 3)  [in OTR-local space]
      2. OUTER INSERT rotate90 @ translate(10, 0): (0, 3) → rotate90=(-3, 0) → (-3+10, 0) = (7, 0)

    Expected world position: (7, 0).
    Wrong order yields (10, 3) — the classic symptom that caught this bug.
    """
    doc = ezdxf.new("R2010")
    inr = doc.blocks.new("INR_ROT")
    inr.add_circle(center=(0, 0, 0), radius=0.2)

    otr = doc.blocks.new("OTR_ROT")
    otr.add_blockref("INR_ROT", insert=(0.0, 3.0))

    outer_ref = doc.modelspace().add_blockref("OTR_ROT", insert=(10.0, 0.0))
    outer_ref.dxf.rotation = 90

    with tempfile.TemporaryDirectory() as tmp:
        circles = _circles(_save(doc, tmp, "nested_rot.dxf"))

    assert len(circles) == 1, f"Expected 1 circle, got {len(circles)}"
    assert abs(circles[0].cx - 7.0) < 0.01, (
        f"Rotation compound X wrong: expected 7.0, got {circles[0].cx:.4f} "
        f"(got 10.0 = parent_m@local_m, want 7.0 = local_m@parent_m)"
    )
    assert abs(circles[0].cy - 0.0) < 0.01, (
        f"Rotation compound Y wrong: expected 0.0, got {circles[0].cy:.4f}"
    )


def test_arc_angles_in_rotated_block():
    """
    Regression for _rotation_from_matrix() returning -θ instead of +θ.

    ezdxf row-vector convention stores -sin(θ) at m[1,0]; the old code used
    sin_a = m[1,0]/sx = -sin(θ), so atan2 returned -θ.  Arc start/end angles
    were offset by -2θ, making arcs in rotated blocks point in wrong direction.

    Setup:
      Block 'ARC_BLK': arc centred at (0,0), start=0°, end=90°  (CCW quarter-circle)
      INSERT 'ARC_BLK' at (0,0) with rotation=90°

    Expected world arc start/end:
      arc start_angle (block) = 0°,   rotated by +90° → world start = 90°
      arc end_angle   (block) = 90°,  rotated by +90° → world end   = 180°

    With the old sign bug (-θ instead of +θ):
      rotation offset = -90°
      world start = 0° + (-90°) = -90°,  world end = 90° + (-90°) = 0°  ← wrong
    """
    doc = ezdxf.new("R2010")
    blk = doc.blocks.new("ARC_BLK")
    blk.add_arc(center=(0, 0, 0), radius=1.0, start_angle=0, end_angle=90)

    ref = doc.modelspace().add_blockref("ARC_BLK", insert=(0.0, 0.0))
    ref.dxf.rotation = 90

    with tempfile.TemporaryDirectory() as tmp:
        arcs = _arcs(_save(doc, tmp, "arc_rot.dxf"))

    assert len(arcs) == 1, f"Expected 1 arc, got {len(arcs)}"
    # Normalise angles to [0, 360)
    start = arcs[0].start_angle % 360
    end   = arcs[0].end_angle   % 360
    assert abs(start - 90.0) < 0.5, (
        f"Arc start_angle wrong: expected 90°, got {start:.2f}° "
        f"(bug gives -90° = 270°)"
    )
    assert abs(end - 180.0) < 0.5, (
        f"Arc end_angle wrong: expected 180°, got {end:.2f}° "
        f"(bug gives 0°)"
    )
