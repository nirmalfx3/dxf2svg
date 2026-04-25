"""
dxf2svg/core/svg_builder.py
────────────────────────────
Converts extracted DXF geometry into clean, normalised SVG.

Features:
  - Auto viewBox from geometry bounding box with configurable padding
  - Y-axis flip (DXF Y-up → SVG Y-down)
  - Layer → CSS class mapping with ACI colour fallback
  - Full <symbol> / <use> mode for building reusable symbol libraries
  - Arc/ellipse path generation (SVG A command)
  - Spline → cubic bezier approximation
  - Configurable stroke widths from layer lineweight
"""

import math
import xml.etree.ElementTree as ET
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass

from .extractor import (
    ExtLine, ExtCircle, ExtArc, ExtPolyline, ExtSpline,
    ExtEllipse, ExtText, ExtSolid, LayerInfo
)

# ACI index → hex colour (subset of AutoCAD Color Index)
ACI_COLORS = {
    1: "#FF0000", 2: "#FFFF00", 3: "#00FF00", 4: "#00FFFF",
    5: "#0000FF", 6: "#FF00FF", 7: "#000000", 8: "#808080",
    9: "#C0C0C0", 30: "#FF7F00", 40: "#FFBF00", 50: "#BFBF00",
    70: "#00BFBF", 110: "#007FFF", 140: "#7F00FF", 170: "#FF007F",
    250: "#333333", 251: "#555555", 252: "#777777",
    253: "#999999", 254: "#BBBBBB", 255: "#DDDDDD",
}

DEFAULT_STROKE  = "#1a1a2e"
DEFAULT_LW_PX   = 1.0

PADDING_FACTOR  = 0.05          # 5 % padding around bounding box

MIN_LW_MM       = 0.25          # minimum stroke lineweight floor (mm)
MM_TO_PX        = 96.0 / 25.4   # CSS: 1 in = 96 px, 1 in = 25.4 mm  → 3.7795 px/mm


@dataclass
class BuildConfig:
    normalize_viewbox: bool = True
    flip_y: bool = True
    target_width: Optional[float] = None   # None = preserve aspect ratio
    target_height: Optional[float] = None
    symbol_mode: bool = False              # Output <symbol> instead of inline
    symbol_id: str = "symbol"
    embed_css: bool = True
    stroke_scale: float = 1.0             # Global stroke width multiplier
    font_family: str = "monospace"
    background: Optional[str] = "white"   # None = transparent
    # Maximum text height as a fraction of the geometry's shorter dimension.
    # Prevents oversized DXF text (e.g. MTEXT char_height designed for a
    # full-drawing context) from covering the symbol geometry in thumbnails.
    # Set to None to disable the cap and render text at its exact DXF height.
    max_text_height_fraction: Optional[float] = 0.15


class SVGBuilder:
    """
    Accepts an iterable of geometry objects from DXFExtractor,
    builds and returns an SVG string.
    """

    def __init__(self, layers: Dict[str, LayerInfo], config: Optional[BuildConfig] = None):
        self.layers = layers
        self.cfg = config or BuildConfig()
        self._entities = []
        self._used_layers: set = set()
        self._layer_attr_cache: Dict[str, Dict[str, str]] = {}
        self.last_raw_extents: Optional[Tuple[float, float]] = None  # (width_in, height_in) pre-padding

    def add_entities(self, entity_iter):
        """Consume extractor output."""
        self._entities = list(entity_iter)

    @property
    def entity_count(self) -> int:
        return len(self._entities)

    def build(self) -> str:
        if not self._entities:
            return self._empty_svg()

        bbox = self._pre_scan()
        if bbox is None:
            return self._empty_svg()

        min_x, min_y, max_x, max_y = bbox
        w = max_x - min_x
        h = max_y - min_y
        self.last_raw_extents = (w, h)   # raw geometry extents before padding
        pad_x = w * PADDING_FACTOR
        pad_y = h * PADDING_FACTOR

        # SVG coordinate space
        vx = min_x - pad_x
        vy = min_y - pad_y
        vw = w + 2 * pad_x
        vh = h + 2 * pad_y

        # Store for font-size capping in _svg_text().
        # When geometry exists, cap text at max_text_height_fraction of the
        # shorter padded viewBox dimension so large DXF text doesn't obscure
        # the symbol.  When there is no non-text geometry (_has_geometry=False),
        # no cap is applied (text IS the content in that case).
        if getattr(self, '_has_geometry', True) and self.cfg.max_text_height_fraction:
            self._geom_shorter_dim = max(min(vw, vh), 1e-6)
        else:
            self._geom_shorter_dim = None

        # Output dimensions: 1:1 with DXF physical coordinates (which are in inches).
        # width/height are always expressed in "in" units so any SVG viewer renders
        # the symbol at its true physical size.
        # The JS showPreview() normalises the preview to 800 px for screen display;
        # vector-effect:non-scaling-stroke keeps stroke widths at their CSS-pixel
        # values regardless of that zoom, so lines stay at the correct weight.
        # target_width / target_height are explicit px overrides for programmatic use.
        if self.cfg.target_width and self.cfg.target_height:
            out_w, out_h = self.cfg.target_width, self.cfg.target_height
        elif self.cfg.target_width:
            out_w = self.cfg.target_width
            out_h = (vh / vw * out_w) if vw > 0 else out_w
        elif self.cfg.target_height:
            out_h = self.cfg.target_height
            out_w = (vw / vh * out_h) if vh > 0 else out_h
        else:
            # 1:1 — DXF inches become SVG inches
            out_w, out_h = vw, vh

        # Root element
        svg = ET.Element("svg")
        svg.set("xmlns", "http://www.w3.org/2000/svg")
        svg.set("xmlns:xlink", "http://www.w3.org/1999/xlink")
        if self.cfg.target_width or self.cfg.target_height:
            svg.set("width",  f"{out_w:.4f}")
            svg.set("height", f"{out_h:.4f}")
        else:
            svg.set("width",  f"{out_w:.4f}in")
            svg.set("height", f"{out_h:.4f}in")
        svg.set("viewBox", f"{vx:.4f} {vy:.4f} {vw:.4f} {vh:.4f}")

        # Background
        if self.cfg.background:
            bg = ET.SubElement(svg, "rect")
            bg.set("x", f"{vx:.4f}"); bg.set("y", f"{vy:.4f}")
            bg.set("width", f"{vw:.4f}"); bg.set("height", f"{vh:.4f}")
            bg.set("fill", self.cfg.background)

        # CSS
        if self.cfg.embed_css:
            style = ET.SubElement(svg, "style")
            style.text = self._build_css()

        # Defs (for symbol mode)
        defs = None
        if self.cfg.symbol_mode:
            defs = ET.SubElement(svg, "defs")
            symbol = ET.SubElement(defs, "symbol")
            symbol.set("id", self.cfg.symbol_id)
            symbol.set("viewBox", f"{vx:.4f} {vy:.4f} {vw:.4f} {vh:.4f}")
            container = symbol
        else:
            container = ET.SubElement(svg, "g")
            container.set("id", "drawing")
            if self.cfg.flip_y:
                # Flip Y axis: SVG origin is top-left, DXF is bottom-left
                container.set(
                    "transform",
                    f"scale(1,-1) translate(0,{-(vy*2 + vh):.4f})"
                )

        # Render all entities
        for entity in self._entities:
            elem = self._render_entity(entity)
            if elem is not None:
                container.append(elem)

        return self._pretty_xml(svg)

    # ── entity renderers ─────────────────────────────────────────────────────

    def _render_entity(self, entity) -> Optional[ET.Element]:
        t = type(entity).__name__
        try:
            if t == "ExtLine":      return self._svg_line(entity)
            if t == "ExtCircle":    return self._svg_circle(entity)
            if t == "ExtArc":       return self._svg_arc(entity)
            if t == "ExtPolyline":  return self._svg_polyline(entity)
            if t == "ExtSpline":    return self._svg_spline(entity)
            if t == "ExtEllipse":   return self._svg_ellipse(entity)
            if t == "ExtText":      return self._svg_text(entity)
            if t == "ExtSolid":     return self._svg_solid(entity)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(f"Render skip {t}: {e}")
        return None

    def _layer_attrs(self, layer_name: str) -> Dict[str, str]:
        info = self.layers.get(layer_name)
        stroke = DEFAULT_STROKE
        lw = DEFAULT_LW_PX * self.cfg.stroke_scale

        if info:
            if info.rgb:
                stroke = "#{:02X}{:02X}{:02X}".format(*info.rgb)
            elif info.color_index in ACI_COLORS:
                stroke = ACI_COLORS[info.color_index]
            lw = max(MIN_LW_MM, info.lineweight) * MM_TO_PX * self.cfg.stroke_scale  # floor at 0.25 mm

        return {
            "class": f"layer-{layer_name.replace(' ', '_').replace('/', '_')}",
            "stroke": stroke,
            "stroke-width": f"{lw:.3f}",
            "fill": "none",
        }

    def _apply_attrs(self, elem: ET.Element, layer: str, color_override=None):
        """Apply layer-derived CSS class and stroke attrs, then apply any entity-level colour."""
        attrs = self._layer_attr_cache.get(layer) or self._layer_attrs(layer)
        for k, v in attrs.items():
            elem.set(k, v)
        # Entity-level colour (true_color or explicit ACI) overrides the layer colour.
        # The inline stroke= attribute has higher specificity than the CSS class rule.
        if color_override is not None:
            elem.set("stroke", "#{:02X}{:02X}{:02X}".format(*color_override))

    def _svg_line(self, e: ExtLine) -> ET.Element:
        el = ET.Element("line")
        el.set("x1", f"{e.x1:.4f}"); el.set("y1", f"{e.y1:.4f}")
        el.set("x2", f"{e.x2:.4f}"); el.set("y2", f"{e.y2:.4f}")
        self._apply_attrs(el, e.layer, e.color)
        return el

    def _svg_circle(self, e: ExtCircle) -> ET.Element:
        if abs(e.rx - e.ry) < 0.001:
            el = ET.Element("circle")
            el.set("cx", f"{e.cx:.4f}"); el.set("cy", f"{e.cy:.4f}")
            el.set("r",  f"{e.rx:.4f}")
        else:
            # Non-uniform scale → ellipse
            el = ET.Element("ellipse")
            el.set("cx", f"{e.cx:.4f}"); el.set("cy", f"{e.cy:.4f}")
            el.set("rx", f"{e.rx:.4f}"); el.set("ry", f"{e.ry:.4f}")
        self._apply_attrs(el, e.layer, e.color)
        return el

    def _svg_arc(self, e: ExtArc) -> ET.Element:
        path = self._arc_to_path(e.cx, e.cy, e.rx, e.ry, e.start_angle, e.end_angle)
        el = ET.Element("path")
        el.set("d", path)
        self._apply_attrs(el, e.layer, e.color)
        return el

    def _svg_polyline(self, e: ExtPolyline) -> ET.Element:
        if not e.points:
            return None
        pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in e.points)
        tag = "polygon" if e.closed else "polyline"
        el = ET.Element(tag)
        el.set("points", pts)
        self._apply_attrs(el, e.layer, e.color)
        return el

    def _svg_spline(self, e: ExtSpline) -> ET.Element:
        if len(e.points) < 2:
            return None
        # Points come from ezdxf flattening() — already dense and accurate.
        # Render as a polyline path; no further curve fitting needed.
        d = "M " + " L ".join(f"{x:.4f},{y:.4f}" for x, y in e.points)
        if e.closed:
            d += " Z"
        el = ET.Element("path")
        el.set("d", d)
        self._apply_attrs(el, e.layer, e.color)
        return el

    def _svg_ellipse(self, e: ExtEllipse) -> ET.Element:
        el = ET.Element("ellipse")
        el.set("cx", f"{e.cx:.4f}"); el.set("cy", f"{e.cy:.4f}")
        el.set("rx", f"{e.rx:.4f}"); el.set("ry", f"{e.ry:.4f}")
        if abs(e.rotation) > 0.001:
            el.set("transform", f"rotate({e.rotation:.4f},{e.cx:.4f},{e.cy:.4f})")
        self._apply_attrs(el, e.layer, e.color)
        return el

    def _svg_text(self, e: ExtText) -> ET.Element:
        el = ET.Element("text")
        el.set("x", f"{e.x:.4f}"); el.set("y", f"{e.y:.4f}")

        # Cap font-size when non-text geometry is present.  DXF char_height can
        # be disproportionately large relative to a block's geometric footprint
        # (e.g. MTEXT designed for a full-drawing context dropped into a small
        # symbol block).  Capping at a fraction of the geometry's shorter dim
        # keeps text readable without it dominating or obscuring the symbol.
        geom_dim = getattr(self, '_geom_shorter_dim', None)
        if geom_dim is not None and self.cfg.max_text_height_fraction:
            font_size = min(e.height, geom_dim * self.cfg.max_text_height_fraction)
        else:
            font_size = e.height
        el.set("font-size", f"{font_size:.4f}")
        el.set("font-family", self.cfg.font_family)

        # Map DXF halign → SVG text-anchor so centered/right text lands
        # on the correct visual position without repositioning the anchor.
        if e.h_align in (1, 4):        # center / middle
            el.set("text-anchor", "middle")
        elif e.h_align == 2:           # right
            el.set("text-anchor", "end")
        # else: left / aligned / fit / default → "start" (SVG default, no attribute needed)

        # Map DXF valign → SVG dominant-baseline
        if e.v_align == 2:             # middle
            el.set("dominant-baseline", "central")
        elif e.v_align == 3:           # top
            el.set("dominant-baseline", "hanging")
        # else: baseline / bottom → SVG auto default, no attribute needed

        # Rotation (negated for Y-flip) + local Y-un-flip transform.
        if abs(e.rotation) > 0.001:
            el.set("transform", f"rotate({-e.rotation:.4f},{e.x:.4f},{e.y:.4f})")
        if self.cfg.flip_y:
            cur = el.get("transform", "")
            flip = f"scale(1,-1) translate(0,{-2*e.y:.4f})"
            el.set("transform", f"{cur} {flip}".strip())

        # Colour: entity override wins over layer colour.
        # IMPORTANT: text does NOT receive a CSS layer class.
        # In SVG, CSS class selectors have higher specificity than presentation
        # attributes, so a rule like ".layer-0 { stroke: #000000 }" would override
        # our stroke="none" presentation attribute and paint unwanted outlines on
        # every glyph.  By omitting the class, we ensure the fill and stroke
        # presentation attributes below are the final authority on text appearance.
        # Lines, circles, arcs etc. continue to use CSS classes normally.
        attrs = self._layer_attr_cache.get(e.layer) or self._layer_attrs(e.layer)
        fill = "#{:02X}{:02X}{:02X}".format(*e.color) if e.color is not None else attrs["stroke"]
        el.set("fill", fill)
        el.set("stroke", "none")

        # Multi-line text: split on any line ending and emit <tspan> children
        # so each line is independently positioned.  Single-line text is set
        # directly as element text (avoids an unnecessary <tspan> wrapper).
        lines = (e.text or "").splitlines()
        if not lines:
            lines = [""]
        if len(lines) == 1:
            el.text = lines[0]
        else:
            for i, line in enumerate(lines):
                tspan = ET.SubElement(el, "tspan")
                tspan.set("x", f"{e.x:.4f}")
                # First tspan: no dy offset (starts at the element's y baseline).
                # Subsequent tspans: 1.5em line advance (matches DXF default
                # MTEXT line spacing of 1.6667× at factor=1.0, approx as 1.5em).
                tspan.set("dy", "0" if i == 0 else "1.5em")
                tspan.text = line if line else "\u00a0"   # NBSP keeps empty lines
        return el

    def _svg_solid(self, e: ExtSolid) -> ET.Element:
        pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in e.points)
        el = ET.Element("polygon")
        el.set("points", pts)
        attrs = self._layer_attr_cache.get(e.layer) or self._layer_attrs(e.layer)
        el.set("class", attrs["class"])
        # Entity colour override for filled shapes: both stroke and fill use it.
        stroke = "#{:02X}{:02X}{:02X}".format(*e.color) if e.color is not None else attrs["stroke"]
        el.set("fill", stroke)
        el.set("stroke", stroke)
        el.set("stroke-width", attrs["stroke-width"])
        return el

    # ── geometry utilities ───────────────────────────────────────────────────

    def _arc_to_path(self, cx, cy, rx, ry, start_deg, end_deg) -> str:
        """Convert DXF arc parameters to SVG path arc command."""
        # Normalise angle range
        while end_deg < start_deg:
            end_deg += 360.0
        delta = end_deg - start_deg
        if delta >= 360.0:
            # Full circle
            return (
                f"M {cx-rx:.4f},{cy:.4f} "
                f"A {rx:.4f},{ry:.4f} 0 1 1 {cx+rx:.4f},{cy:.4f} "
                f"A {rx:.4f},{ry:.4f} 0 1 1 {cx-rx:.4f},{cy:.4f} Z"
            )

        s_rad = math.radians(start_deg)
        e_rad = math.radians(end_deg)
        x1 = cx + rx * math.cos(s_rad)
        y1 = cy + ry * math.sin(s_rad)
        x2 = cx + rx * math.cos(e_rad)
        y2 = cy + ry * math.sin(e_rad)
        large = 1 if delta > 180.0 else 0
        return f"M {x1:.4f},{y1:.4f} A {rx:.4f},{ry:.4f} 0 {large} 1 {x2:.4f},{y2:.4f}"

    # ── pre-scan: bbox + used layers + attr cache (single pass) ─────────────

    def _pre_scan(self):
        """
        Single pass over entities that computes:
          1. Geometry bounding box (min/max x,y) — text anchor points excluded so
             that oversized DXF text does not inflate the viewBox scale.
          2. Text-anchor fallback bbox — used only when there is no non-text geometry
             (e.g. text-only test SVGs).
          3. Used layer names set + layer attribute cache (pre-computed once).

        Sets self._has_geometry so _svg_text can decide whether to apply the
        max_text_height_fraction cap.

        Returns (min_x, min_y, max_x, max_y) or None if no entities found.
        """
        # Geometry bbox (lines, circles, arcs, polylines…)
        min_x = min_y = float("inf")
        max_x = max_y = float("-inf")
        # Text-anchor fallback: only used when there is no non-text geometry
        txt_min_x = txt_min_y = float("inf")
        txt_max_x = txt_max_y = float("-inf")
        used: set = set()

        for e in self._entities:
            if hasattr(e, "layer"):
                used.add(e.layer)
            t = type(e).__name__
            try:
                if t == "ExtLine":
                    if e.x1 < min_x: min_x = e.x1
                    if e.x2 < min_x: min_x = e.x2
                    if e.x1 > max_x: max_x = e.x1
                    if e.x2 > max_x: max_x = e.x2
                    if e.y1 < min_y: min_y = e.y1
                    if e.y2 < min_y: min_y = e.y2
                    if e.y1 > max_y: max_y = e.y1
                    if e.y2 > max_y: max_y = e.y2
                elif t in ("ExtCircle", "ExtEllipse", "ExtArc"):
                    lx = e.cx - e.rx; rx = e.cx + e.rx
                    ly = e.cy - e.ry; ry = e.cy + e.ry
                    if lx < min_x: min_x = lx
                    if rx > max_x: max_x = rx
                    if ly < min_y: min_y = ly
                    if ry > max_y: max_y = ry
                elif t in ("ExtPolyline", "ExtSpline", "ExtSolid"):
                    for px, py in e.points:
                        if px < min_x: min_x = px
                        if px > max_x: max_x = px
                        if py < min_y: min_y = py
                        if py > max_y: max_y = py
                elif t == "ExtText":
                    # Text anchor points do NOT enter the geometry bbox.
                    # They are stored separately and used only as a fallback
                    # when the drawing has no non-text geometry at all.
                    if e.x < txt_min_x: txt_min_x = e.x
                    if e.x > txt_max_x: txt_max_x = e.x
                    if e.y < txt_min_y: txt_min_y = e.y
                    if e.y > txt_max_y: txt_max_y = e.y
            except Exception:
                pass

        self._used_layers = used
        self._layer_attr_cache = {name: self._layer_attrs(name) for name in used}

        if min_x != float("inf"):
            # Normal case: geometry found → geometry drives the viewBox.
            self._has_geometry = True
            return min_x, min_y, max_x, max_y

        if txt_min_x != float("inf"):
            # Text-only content (e.g. test SVGs with just TEXT/MTEXT).
            # No geometry-based cap is applied in this case.
            self._has_geometry = False
            return txt_min_x, txt_min_y, txt_max_x, txt_max_y

        return None

    # ── CSS generation ───────────────────────────────────────────────────────

    def _build_css(self) -> str:
        lines = [
            "svg { font-family: monospace; }",
            # stroke-width is in CSS pixels; non-scaling-stroke keeps it at that
            # physical size regardless of how the SVG is zoomed or normalised.
            "line, polyline, polygon, path, circle, ellipse"
            " { vector-effect: non-scaling-stroke; }",
        ]
        for name in sorted(self._used_layers):
            info = self.layers.get(name)
            cls = f"layer-{name.replace(' ', '_').replace('/', '_')}"
            stroke = DEFAULT_STROKE
            if info:
                if info.rgb:
                    stroke = "#{:02X}{:02X}{:02X}".format(*info.rgb)
                elif info.color_index in ACI_COLORS:
                    stroke = ACI_COLORS[info.color_index]
            lines.append(f".{cls} {{ stroke: {stroke}; }}")
        return "\n        ".join(lines)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _empty_svg(self) -> str:
        return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text x="10" y="50" font-size="10" fill="red">No geometry extracted</text></svg>'

    def _pretty_xml(self, root: ET.Element) -> str:
        ET.indent(root, space="  ")
        return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
