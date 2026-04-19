"""
tests/test_buildconfig.py
──────────────────────────
Framework-health tests for BuildConfig (DX-FH-004).

Run from G:/dxf2svg/:
    G:/dxf2svg/.venv/Scripts/pytest tests/test_buildconfig.py -v
"""

import pytest
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dxf2svg.core.svg_builder import BuildConfig


# ── DX-FH-004: preserve_size removed ─────────────────────────────────────────

def test_preserve_size_field_removed():
    """BuildConfig must not expose a preserve_size field (DX-FH-004)."""
    cfg = BuildConfig()
    assert not hasattr(cfg, "preserve_size"), (
        "preserve_size is still present on BuildConfig — remove the dead field"
    )


def test_preserve_size_kwarg_raises():
    """Passing preserve_size=True to BuildConfig must raise TypeError (DX-FH-004)."""
    with pytest.raises(TypeError):
        BuildConfig(preserve_size=True)


def test_default_buildconfig_is_valid():
    """BuildConfig() with no args must instantiate without error."""
    cfg = BuildConfig()
    assert cfg.flip_y is True
    assert cfg.background == "white"
    assert cfg.stroke_scale == 1.0
    assert cfg.embed_css is True
    assert cfg.symbol_mode is False
