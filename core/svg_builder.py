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

DEFAULT_STROKE = "#1a1a2e"
DEFAULT_LW_PX  = 1.0
PADDING_FACTOR = 0.05   # 5% padding around bounding box


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
    background: Optional[str] = None      # None = transparent


class SVGBuilder:
    """
    Accepts an iterable of geometry objects from DXFExtractor,
    builds and returns an SVG string.
    """

    def __init__(self, layers: Dict[str, LayerInfo], config: Optional[BuildConfig] = None):
        self.layers = layers
        self.cfg = config or BuildConfig()
        self._entities = []

    def add_entities(self, entity_iter):
        """Consume extractor output."""
        self._entities = list(entity_iter)

    def build(self) -> str:
        if not self._entities:
            return self._empty_svg()

        bbox = self._compute_bbox()
        if bbox is None:
            return self._empty_svg()

        min_x, min_y, max_x, max_y = bbox
        w = max_x - min_x
        h = max_y - min_y
        pad_x = w * PADDING_FACTOR
        pad_y = h * PADDING_FACTOR

        # SVG coordinate space
        vx = min_x - pad_x
        vy = min_y - pad_y
        vw = w + 2 * pad_x
        vh = h + 2 * pad_y

        # Resolve target size
        out_w = self.cfg.target_width  or vw
        out_h = self.cfg.target_height or vh

        # Root element
        svg = ET.Element("svg")
        svg.set("xmlns", "http://www.w3.org/2000/svg")
        svg.set("xmlns:xlink", "http://www.w3.org/1999/xlink")
        svg.set("width",   f"{out_w:.4f}")
        svg.set("height",  f"{out_h:.4f}")
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
            lw = max(0.5, info.lineweight * 3.78) * self.cfg.stroke_scale  # mm→px approx

        return {
            "class": f"layer-{layer_name.replace(' ', '_').replace('/', '_')}",
            "stroke": stroke,
            "stroke-width": f"{lw:.3f}",
            "fill": "none",
        }

    def _apply_attrs(self, elem: ET.Element, layer: str):
        for k, v in self._layer_attrs(layer).items():
            elem.set(k, v)

    def _svg_line(self, e: ExtLine) -> ET.Element:
        el = ET.Element("line")
        el.set("x1", f"{e.x1:.4f}"); el.set("y1", f"{e.y1:.4f}")
        el.set("x2", f"{e.x2:.4f}"); el.set("y2", f"{e.y2:.4f}")
        self._apply_attrs(el, e.layer)
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
        self._apply_attrs(el, e.layer)
        return el

    def _svg_arc(self, e: ExtArc) -> ET.Element:
        path = self._arc_to_path(e.cx, e.cy, e.rx, e.ry, e.start_angle, e.end_angle)
        el = ET.Element("path")
        el.set("d", path)
        self._apply_attrs(el, e.layer)
        return el

    def _svg_polyline(self, e: ExtPolyline) -> ET.Element:
        if not e.points:
            return None
        pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in e.points)
        tag = "polygon" if e.closed else "polyline"
        el = ET.Element(tag)
        el.set("points", pts)
        self._apply_attrs(el, e.layer)
        return el

    def _svg_spline(self, e: ExtSpline) -> ET.Element:
        if len(e.points) < 2:
            return None
        d = self._spline_to_path(e.points, e.closed)
        el = ET.Element("path")
        el.set("d", d)
        self._apply_attrs(el, e.layer)
        return el

    def _svg_ellipse(self, e: ExtEllipse) -> ET.Element:
        el = ET.Element("ellipse")
        el.set("cx", f"{e.cx:.4f}"); el.set("cy", f"{e.cy:.4f}")
        el.set("rx", f"{e.rx:.4f}"); el.set("ry", f"{e.ry:.4f}")
        if abs(e.rotation) > 0.001:
            el.set("transform", f"rotate({e.rotation:.4f},{e.cx:.4f},{e.cy:.4f})")
        self._apply_attrs(el, e.layer)
        return el

    def _svg_text(self, e: ExtText) -> ET.Element:
        el = ET.Element("text")
        el.set("x", f"{e.x:.4f}"); el.set("y", f"{e.y:.4f}")
        el.set("font-size", f"{e.height:.4f}")
        el.set("font-family", self.cfg.font_family)
        if abs(e.rotation) > 0.001:
            el.set("transform", f"rotate({-e.rotation:.4f},{e.x:.4f},{e.y:.4f})")
        if self.cfg.flip_y:
            cur = el.get("transform", "")
            flip = f"scale(1,-1) translate(0,{-2*e.y:.4f})"
            el.set("transform", f"{cur} {flip}".strip())
        attrs = self._layer_attrs(e.layer)
        el.set("class", attrs["class"])
        el.set("fill", attrs["stroke"])
        el.set("stroke", "none")
        el.text = e.text
        return el

    def _svg_solid(self, e: ExtSolid) -> ET.Element:
        pts = " ".join(f"{x:.4f},{y:.4f}" for x, y in e.points)
        el = ET.Element("polygon")
        el.set("points", pts)
        attrs = self._layer_attrs(e.layer)
        el.set("class", attrs["class"])
        el.set("fill", attrs["stroke"])
        el.set("stroke", attrs["stroke"])
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

    def _spline_to_path(self, points: list, closed: bool) -> str:
        """Approximate spline as cubic bezier via Catmull-Rom conversion."""
        if len(points) == 2:
            x1, y1 = points[0]; x2, y2 = points[1]
            return f"M {x1:.4f},{y1:.4f} L {x2:.4f},{y2:.4f}"

        def catmull_to_bezier(p0, p1, p2, p3, alpha=0.5):
            def tj(ti, pi, pj):
                dx = pj[0]-pi[0]; dy = pj[1]-pi[1]
                return ti + (dx*dx + dy*dy) ** (alpha * 0.5)
            t0=0; t1=tj(t0,p0,p1); t2=tj(t1,p1,p2); t3=tj(t2,p2,p3)
            if t1==t0: t1=t0+1e-6
            if t2==t1: t2=t1+1e-6
            if t3==t2: t3=t2+1e-6
            c1x = (t2-t1)/(t2-t0) * ((p1[0]-p0[0])/(t1-t0) - (p2[0]-p0[0])/(t2-t0)) + (p2[0]-p1[0])/(t2-t1)
            c1y = (t2-t1)/(t2-t0) * ((p1[1]-p0[1])/(t1-t0) - (p2[1]-p0[1])/(t2-t0)) + (p2[1]-p1[1])/(t2-t1)
            c2x = (t2-t1)/(t3-t1) * ((p3[0]-p2[0])/(t3-t2) - (p3[0]-p1[0])/(t3-t1)) + (p2[0]-p1[0])/(t2-t1)
            c2y = (t2-t1)/(t3-t1) * ((p3[1]-p2[1])/(t3-t2) - (p3[1]-p1[1])/(t3-t1)) + (p2[1]-p1[1])/(t2-t1)
            bx1 = p1[0] + c1x * (t2-t1)/3
            by1 = p1[1] + c1y * (t2-t1)/3
            bx2 = p2[0] - c2x * (t2-t1)/3
            by2 = p2[1] - c2y * (t2-t1)/3
            return (bx1, by1, bx2, by2)

        if closed:
            pts = [points[-1]] + points + [points[0], points[1]]
        else:
            pts = [points[0]] + points + [points[-1]]

        d = f"M {pts[1][0]:.4f},{pts[1][1]:.4f}"
        for i in range(1, len(pts)-2):
            bx1, by1, bx2, by2 = catmull_to_bezier(pts[i-1], pts[i], pts[i+1], pts[i+2])
            ex, ey = pts[i+1]
            d += f" C {bx1:.4f},{by1:.4f} {bx2:.4f},{by2:.4f} {ex:.4f},{ey:.4f}"
        if closed:
            d += " Z"
        return d

    # ── bounding box ─────────────────────────────────────────────────────────

    def _compute_bbox(self):
        xs, ys = [], []

        for e in self._entities:
            t = type(e).__name__
            try:
                if t == "ExtLine":
                    xs += [e.x1, e.x2]; ys += [e.y1, e.y2]
                elif t in ("ExtCircle", "ExtEllipse"):
                    xs += [e.cx - e.rx, e.cx + e.rx]
                    ys += [e.cy - e.ry, e.cy + e.ry]
                elif t == "ExtArc":
                    xs += [e.cx - e.rx, e.cx + e.rx]
                    ys += [e.cy - e.ry, e.cy + e.ry]
                elif t in ("ExtPolyline", "ExtSpline", "ExtSolid"):
                    pts = e.points
                    xs += [p[0] for p in pts]; ys += [p[1] for p in pts]
                elif t == "ExtText":
                    xs.append(e.x); ys.append(e.y)
            except Exception:
                pass

        if not xs:
            return None
        return min(xs), min(ys), max(xs), max(ys)

    # ── CSS generation ───────────────────────────────────────────────────────

    def _build_css(self) -> str:
        lines = [
            "svg { font-family: monospace; }",
            "line, polyline, polygon, path, circle, ellipse { vector-effect: non-scaling-stroke; }",
        ]
        used_layers = {e.layer for e in self._entities if hasattr(e, "layer")}
        for name in sorted(used_layers):
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
