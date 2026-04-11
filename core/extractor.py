"""
dxf2svg/core/extractor.py
─────────────────────────
Full DXF entity extractor with:
  - Recursive nested INSERT / block traversal
  - Accumulated Matrix44 transform chain (translate, rotate, scale)
  - Handles: LINE, CIRCLE, ARC, LWPOLYLINE, POLYLINE, SPLINE,
             ELLIPSE, TEXT, MTEXT, SOLID, HATCH (boundary only)
  - Emits layer metadata for CSS class mapping
"""

import math
import logging
from dataclasses import dataclass, field
from typing import Iterator, Optional, Tuple, List

import ezdxf
from ezdxf.math import Matrix44, Vec3
from ezdxf.entities import Insert

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Data classes — geometry carriers
# ─────────────────────────────────────────────

@dataclass
class LayerInfo:
    name: str
    color_index: int = 7       # ACI colour (7 = white/black)
    rgb: Optional[Tuple] = None
    linetype: str = "CONTINUOUS"
    lineweight: float = 0.25   # mm

@dataclass
class ExtLine:
    x1: float; y1: float
    x2: float; y2: float
    layer: str

@dataclass
class ExtCircle:
    cx: float; cy: float
    rx: float              # x-radius (may differ from ry when parent INSERT has xscale≠yscale)
    ry: float              # y-radius
    layer: str

@dataclass
class ExtArc:
    cx: float; cy: float
    rx: float; ry: float
    start_angle: float     # degrees, already rotated by parent transform
    end_angle: float
    layer: str

@dataclass
class ExtPolyline:
    points: List[Tuple[float, float]]
    closed: bool
    layer: str

@dataclass
class ExtSpline:
    points: List[Tuple[float, float]]   # flattened control/fit points
    closed: bool
    layer: str

@dataclass
class ExtEllipse:
    cx: float; cy: float
    rx: float; ry: float
    rotation: float   # degrees
    start_param: float
    end_param: float
    layer: str

@dataclass
class ExtText:
    x: float; y: float
    text: str
    height: float
    rotation: float
    layer: str
    is_mtext: bool = False

@dataclass
class ExtSolid:
    points: List[Tuple[float, float]]
    layer: str


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def _build_insert_matrix(ins: Insert) -> Matrix44:
    """Compose the local transform for a single INSERT entity."""
    dxf = ins.dxf
    tx = dxf.get("insert", Vec3(0, 0, 0))
    rot = math.radians(dxf.get("rotation", 0.0))
    sx  = dxf.get("xscale", 1.0)
    sy  = dxf.get("yscale", 1.0)
    sz  = dxf.get("zscale", 1.0)

    m = (
        Matrix44.translate(tx.x, tx.y, tx.z)
        @ Matrix44.z_rotate(rot)
        @ Matrix44.scale(sx, sy, sz)
    )
    return m


def _scale_from_matrix(m: Matrix44) -> Tuple[float, float]:
    """Extract effective X and Y scale magnitudes from a Matrix44."""
    sx = math.sqrt(m[0, 0]**2 + m[1, 0]**2 + m[2, 0]**2)
    sy = math.sqrt(m[0, 1]**2 + m[1, 1]**2 + m[2, 1]**2)
    return sx, sy


def _rotation_from_matrix(m: Matrix44) -> float:
    """Extract Z-rotation (degrees) from matrix, accounting for scale."""
    sx, _ = _scale_from_matrix(m)
    if sx == 0:
        return 0.0
    cos_a = m[0, 0] / sx
    sin_a = m[1, 0] / sx
    return math.degrees(math.atan2(sin_a, cos_a))


def _transform_pt(m: Matrix44, x: float, y: float) -> Tuple[float, float]:
    v = m.transform(Vec3(x, y, 0))
    return (v.x, v.y)


# ─────────────────────────────────────────────
# Main extractor
# ─────────────────────────────────────────────

class DXFExtractor:
    """
    Walk the DXF block hierarchy and yield normalised geometry objects.

    Usage:
        extractor = DXFExtractor("path/to/file.dxf")
        for entity in extractor.extract():
            ...
    """

    def __init__(self, dxf_path: str, unfold_all_layers: bool = True):
        logger.info(f"Loading DXF: {dxf_path}")
        self.doc = ezdxf.readfile(dxf_path)
        self.unfold_all_layers = unfold_all_layers
        self._layers: dict[str, LayerInfo] = {}
        self._visited_blocks: set = set()   # cycle guard
        self._audit: dict = {}              # block→entity-type counts for debug
        self._load_layers()

    # ── layer metadata ──────────────────────────────────────────────────────

    def _load_layers(self):
        for layer in self.doc.layers:
            name = layer.dxf.name
            aci = layer.dxf.get("color", 7)
            rgb = None
            try:
                from ezdxf.colors import DXF_DEFAULT_COLORS
                if 0 < aci < 256:
                    hex_color = DXF_DEFAULT_COLORS.get(aci)
                    if hex_color:
                        r = (hex_color >> 16) & 0xFF
                        g = (hex_color >> 8)  & 0xFF
                        b = hex_color         & 0xFF
                        rgb = (r, g, b)
            except Exception:
                pass

            lw_raw = layer.dxf.get("lineweight", -3)
            # lineweight is stored in units of 0.01mm; -3 = BYLAYER default
            lw_mm = (lw_raw / 100.0) if lw_raw > 0 else 0.25

            self._layers[name] = LayerInfo(
                name=name,
                color_index=aci,
                rgb=rgb,
                linetype=layer.dxf.get("linetype", "CONTINUOUS"),
                lineweight=lw_mm,
            )
            if self.unfold_all_layers:
                layer.on()
                layer.unlock()

    @property
    def layers(self) -> dict:
        return self._layers

    @property
    def audit(self) -> dict:
        return self._audit

    # ── public entry point ───────────────────────────────────────────────────

    def extract(self, block_name: str = "*Model_Space") -> Iterator:
        """Yield all geometry from the specified block (default: model space)."""
        self._visited_blocks.clear()
        self._audit.clear()
        yield from self._walk_block(block_name, Matrix44())

    def extract_block(self, block_name: str) -> Iterator:
        """Extract a named block in isolation (for symbol library building)."""
        self._visited_blocks.clear()
        yield from self._walk_block(block_name, Matrix44())

    def list_blocks(self) -> List[str]:
        """Return all non-system block names in the DXF."""
        return [
            b.name for b in self.doc.blocks
            if not b.name.startswith("*")
        ]

    # ── recursive block walker ───────────────────────────────────────────────

    def _walk_block(self, block_name: str, parent_m: Matrix44) -> Iterator:
        block = self.doc.blocks.get(block_name)
        if block is None:
            logger.warning(f"Block not found: '{block_name}'")
            return

        # Cycle guard (malformed DXF self-referencing blocks)
        key = (block_name, id(parent_m))
        if block_name in self._visited_blocks and block_name != "*Model_Space":
            logger.debug(f"Cycle guard hit for block: {block_name}")
            return

        if block_name != "*Model_Space":
            self._visited_blocks.add(block_name)

        # Audit tracking
        if block_name not in self._audit:
            self._audit[block_name] = {}

        for entity in block:
            t = entity.dxftype()
            self._audit[block_name][t] = self._audit[block_name].get(t, 0) + 1

            try:
                if t == "INSERT":
                    yield from self._handle_insert(entity, parent_m)
                elif t == "LINE":
                    yield from self._handle_line(entity, parent_m)
                elif t == "CIRCLE":
                    yield from self._handle_circle(entity, parent_m)
                elif t == "ARC":
                    yield from self._handle_arc(entity, parent_m)
                elif t == "LWPOLYLINE":
                    yield from self._handle_lwpolyline(entity, parent_m)
                elif t == "POLYLINE":
                    yield from self._handle_polyline(entity, parent_m)
                elif t == "SPLINE":
                    yield from self._handle_spline(entity, parent_m)
                elif t == "ELLIPSE":
                    yield from self._handle_ellipse(entity, parent_m)
                elif t in ("TEXT", "MTEXT"):
                    yield from self._handle_text(entity, parent_m, is_mtext=(t == "MTEXT"))
                elif t == "SOLID":
                    yield from self._handle_solid(entity, parent_m)
                elif t == "HATCH":
                    yield from self._handle_hatch(entity, parent_m)
                # ATTDEF, ATTRIB, SEQEND, VIEWPORT, DIMSTYLE intentionally skipped
            except Exception as e:
                logger.warning(f"Skipped {t} in '{block_name}': {e}")

        if block_name != "*Model_Space":
            self._visited_blocks.discard(block_name)

    # ── entity handlers ──────────────────────────────────────────────────────

    def _handle_insert(self, ins, parent_m):
        local_m  = _build_insert_matrix(ins)
        compound = parent_m @ local_m
        child_block = ins.dxf.name
        logger.debug(f"  INSERT → '{child_block}'")
        yield from self._walk_block(child_block, compound)

    def _handle_line(self, e, m):
        s = e.dxf.start
        end = e.dxf.end
        x1, y1 = _transform_pt(m, s.x, s.y)
        x2, y2 = _transform_pt(m, end.x, end.y)
        yield ExtLine(x1, y1, x2, y2, e.dxf.get("layer", "0"))

    def _handle_circle(self, e, m):
        c = e.dxf.center
        cx, cy = _transform_pt(m, c.x, c.y)
        sx, sy = _scale_from_matrix(m)
        r = e.dxf.radius
        yield ExtCircle(cx, cy, r * sx, r * sy, e.dxf.get("layer", "0"))

    def _handle_arc(self, e, m):
        c = e.dxf.center
        cx, cy = _transform_pt(m, c.x, c.y)
        sx, sy = _scale_from_matrix(m)
        rot = _rotation_from_matrix(m)
        r = e.dxf.radius
        yield ExtArc(
            cx, cy,
            r * sx, r * sy,
            e.dxf.start_angle + rot,
            e.dxf.end_angle   + rot,
            e.dxf.get("layer", "0")
        )

    def _handle_lwpolyline(self, e, m):
        pts = [_transform_pt(m, p[0], p[1]) for p in e.get_points("xy")]
        closed = bool(e.dxf.get("flags", 0) & 1)
        yield ExtPolyline(pts, closed, e.dxf.get("layer", "0"))

    def _handle_polyline(self, e, m):
        pts = []
        for vertex in e.vertices:
            p = vertex.dxf.location
            pts.append(_transform_pt(m, p.x, p.y))
        closed = bool(e.dxf.get("flags", 0) & 1)
        yield ExtPolyline(pts, closed, e.dxf.get("layer", "0"))

    def _handle_spline(self, e, m):
        # flattening() evaluates the actual B-spline curve into dense segments.
        # Using raw control/fit points instead would give wrong shapes because
        # B-spline control points are NOT on the curve.
        try:
            pts_raw = list(e.flattening(0.01))  # max 0.01-unit deviation
        except Exception:
            try:
                pts_raw = list(e.fit_points) if e.fit_points else list(e.control_points)
            except Exception:
                pts_raw = list(e.control_points)
        pts = [_transform_pt(m, p[0], p[1]) for p in pts_raw]
        closed = bool(e.dxf.get("flags", 0) & 1)
        yield ExtSpline(pts, closed, e.dxf.get("layer", "0"))

    def _handle_ellipse(self, e, m):
        c  = e.dxf.center
        cx, cy = _transform_pt(m, c.x, c.y)
        major = e.dxf.major_axis        # Vec3
        ratio = e.dxf.ratio             # minor/major
        sx, sy = _scale_from_matrix(m)
        rx = math.sqrt(major.x**2 + major.y**2) * sx
        ry = rx * ratio * (sy / sx if sx else 1)
        rot = math.degrees(math.atan2(major.y, major.x)) + _rotation_from_matrix(m)
        yield ExtEllipse(
            cx, cy, rx, ry, rot,
            e.dxf.start_param,
            e.dxf.end_param,
            e.dxf.get("layer", "0")
        )

    def _handle_text(self, e, m, is_mtext=False):
        if is_mtext:
            insert = e.dxf.insert
            text = e.plain_mtext()
            height = e.dxf.get("char_height", 2.5)
            rotation = e.dxf.get("rotation", 0.0)
        else:
            insert = e.dxf.insert
            text = e.dxf.text
            height = e.dxf.get("height", 2.5)
            rotation = e.dxf.get("rotation", 0.0)

        x, y = _transform_pt(m, insert.x, insert.y)
        _, sy = _scale_from_matrix(m)
        rot_offset = _rotation_from_matrix(m)
        yield ExtText(
            x, y, text,
            height * sy,
            rotation + rot_offset,
            e.dxf.get("layer", "0"),
            is_mtext
        )

    def _handle_solid(self, e, m):
        corners = [
            _transform_pt(m, e.dxf.vtx0.x, e.dxf.vtx0.y),
            _transform_pt(m, e.dxf.vtx1.x, e.dxf.vtx1.y),
            _transform_pt(m, e.dxf.vtx2.x, e.dxf.vtx2.y),
            _transform_pt(m, e.dxf.vtx3.x, e.dxf.vtx3.y),
        ]
        yield ExtSolid(corners, e.dxf.get("layer", "0"))

    def _handle_hatch(self, e, m):
        # Emit hatch boundary polylines only (fill is handled in SVG layer)
        try:
            for path in e.paths.paths:
                if hasattr(path, "vertices"):
                    pts = [_transform_pt(m, v.x, v.y) for v in path.vertices]
                    if pts:
                        yield ExtPolyline(pts, True, e.dxf.get("layer", "0"))
                elif hasattr(path, "edges"):
                    for edge in path.edges:
                        if edge.EDGE_TYPE == "LineEdge":
                            x1, y1 = _transform_pt(m, edge.start.x, edge.start.y)
                            x2, y2 = _transform_pt(m, edge.end.x, edge.end.y)
                            yield ExtLine(x1, y1, x2, y2, e.dxf.get("layer", "0"))
        except Exception as ex:
            logger.debug(f"Hatch boundary skip: {ex}")
