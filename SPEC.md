# dxf2svg — Product Specification

> **Revision:** 2026-04-22 · v1.5 — ACI 7 → black; text height cap (15% of geometry shorter dim); geometry-only viewBox; MTEXT multi-line tspan; text no CSS class; entity color; text alignment

---

## Table of Contents

| § | Title | Lines |
|---|-------|-------|
| 1 | [Overview](#1-overview) | 24 – 66 |
| 2 | [Supported DXF Entities](#2-supported-dxf-entities) | 67 – 113 |
| 3 | [BuildConfig — Conversion Options](#3-buildconfig--conversion-options) | 114 – 153 |
| 4 | [Architecture — Data Flow](#4-architecture--data-flow) | 154 – 218 |
| 5 | [Python API Reference](#5-python-api-reference) | 219 – 343 |
| 6 | [CLI Reference](#6-cli-reference) | 344 – 412 |
| 7 | [Web API Reference](#7-web-api-reference) | 413 – 479 |
| 8 | [Web UI](#8-web-ui) | 480 – 549 |
| 9 | [Integration with ELiGen](#9-integration-with-eligen) | 550 – 581 |
| 10 | [Known Limitations](#10-known-limitations) | 582 – 596 |
| 11 | [Key Conventions](#11-key-conventions) | 597 – 616 |
| 12 | [Running dxf2svg](#12-running-dxf2svg) | 617 – end |

> `Read SPEC.md limit=20` for this index · `Read SPEC.md offset=<start> limit=<count>` for any section

---

## 1. Overview

### Mission

dxf2svg converts AutoCAD DXF files to clean, normalized SVG output. It handles nested block hierarchies with full matrix transform accumulation, maps DXF layers to CSS classes, and normalizes output dimensions to screen-ready pixel sizes.

**Core value propositions:**

- No AutoCAD license required — reads open-format DXF (any version ezdxf supports)
- Handles nested INSERT/block hierarchies to arbitrary depth with correct transform chains
- Three interfaces for different workflows: Python API, CLI, and drag-drop web UI
- Produces well-structured SVG with per-layer CSS classes, correct Y-flip, and viewBox normalization
- Used by ELiGen to generate SVG thumbnails for its Symbol Library Admin UI

### Tech Stack

| Layer | Technology |
|-------|-----------|
| Language | Python 3.11+ |
| DXF parsing | ezdxf ≥ 1.3 |
| Web server | Flask ≥ 3.0 |
| SVG generation | stdlib `xml.etree.ElementTree` |
| Configuration | stdlib `dataclasses` (`BuildConfig`) |

**Deviation from suite default:** Uses Flask instead of FastAPI — this is a thin file-upload bridge with no structured API contracts. No Pydantic models are needed; all configuration is via the `BuildConfig` dataclass.

### Operation Modes

| Mode | Entry Point | Use Case |
|------|-------------|----------|
| Python API | `from dxf2svg import DXFConverter, BuildConfig` | ELiGen integration, scripting, batch processing |
| CLI | `python -m dxf2svg <command>` | One-off conversions, auditing, CI pipelines |
| Web UI | `python dxf2svg/server.py` → `http://localhost:5000` | Interactive conversion with live preview |

### Project Location

```
G:\dxf2svg\
GitHub: https://github.com/nirmalfx3/dxf2svg
```

---

## 2. Supported DXF Entities

### Rendered Entities

| DXF Entity | SVG Output | Notes |
|-----------|-----------|-------|
| `LINE` | `<line>` | Full transform chain applied |
| `CIRCLE` | `<circle>` | Becomes `<ellipse>` when parent INSERT has non-uniform x/y scale |
| `ARC` | `<path>` (A command) | Start/end angles rotated by accumulated parent transforms |
| `LWPOLYLINE` | `<line>` / `<path>` (A command) | Decomposed via `virtual_entities()` into Line and Arc segments — bulge values expanded to true arcs |
| `POLYLINE` | `<line>` / `<path>` (A command) | Same decomposition as LWPOLYLINE via `virtual_entities()` |
| `SPLINE` | `<path>` (L commands) | Evaluated by ezdxf `flattening(0.01)` into dense accurate segments; **not** Catmull-Rom |
| `ELLIPSE` | `<ellipse>` | Rotation angle preserved via `transform="rotate(...)"` |
| `TEXT` | `<text>` | Height scaled, rotation applied, Y-flip corrected per element |
| `MTEXT` | `<text>` | Same as TEXT; rich formatting stripped |
| `SOLID` | `<polygon>` | Filled with layer stroke color |
| `HATCH` | `<polyline>` | Boundary extraction only — fill pattern not rendered |
| `INSERT` (nested block) | Recursively resolved | Full Matrix44 chain via `ins.matrix44()` (handles base_point, rotation, scale); cycle guard prevents infinite loops; same block can appear at multiple positions |

### Skipped Entities

| Entity | Reason |
|--------|--------|
| OLE objects | Not representable in SVG |
| Raster images | Out of scope for vector conversion |
| 3D solids / meshes | Z-coordinates ignored — 2D converter only |
| Complex linetypes | Rendered as solid stroke |
| ATTRIB text (attributed INSERTs) | Extracted only if ATTRIB is TEXT-compatible |

### Geometry Data Classes (extractor output)

All entities are normalized into these dataclasses before SVG rendering:

| Class | Fields |
|-------|--------|
| `ExtLine` | `x1, y1, x2, y2, layer, color: Optional[Tuple[int,int,int]]` |
| `ExtCircle` | `cx, cy, rx, ry, layer, color` |
| `ExtArc` | `cx, cy, rx, ry, start_angle, end_angle, layer, color` |
| `ExtPolyline` | `points: List[(x,y)], closed: bool, layer, color` — emitted only for HATCH boundary paths; LWPOLYLINE/POLYLINE decompose to `ExtLine`/`ExtArc` |
| `ExtSpline` | `points: List[(x,y)], closed: bool, layer, color` — dense evaluated points from `flattening()` |
| `ExtEllipse` | `cx, cy, rx, ry, rotation, layer, color` |
| `ExtText` | `x, y, text, height, rotation, layer, is_mtext, color, h_align: int, v_align: int` |
| `ExtSolid` | `points: List[(x,y)], layer, color` |
| `LayerInfo` | `name, color_index (ACI), rgb: Tuple, linetype, lineweight (mm)` |

`color = None` means BYLAYER — the layer's own `rgb` is used. `color = (R, G, B)` is an entity-level override (true_color group code 420 or explicit ACI 1–255). See Convention 11.

---

## 3. BuildConfig — Conversion Options

`BuildConfig` is a stdlib dataclass (`core/svg_builder.py`). All fields have defaults — construction with no arguments produces sensible output.

```python
from dxf2svg import BuildConfig

cfg = BuildConfig(
    flip_y        = True,
    stroke_scale  = 1.0,
    target_width  = None,
    target_height = None,
    background    = None,
    embed_css     = True,
    symbol_mode   = False,
    symbol_id     = "symbol",
    font_family   = "monospace",
)
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `flip_y` | `bool` | `True` | Flip Y-axis. DXF uses bottom-left origin; SVG uses top-left. **Always True for visual correctness.** |
| `stroke_scale` | `float` | `1.0` | Global stroke width multiplier. Applied on top of layer lineweight. |
| `target_width` | `float \| None` | `None` | Explicit output width in px. `None` = use physical "in" units (see below). |
| `target_height` | `float \| None` | `None` | Explicit output height in px. `None` = derive from aspect ratio or use physical units. |
| `background` | `str \| None` | `"white"` | Background fill color (e.g. `"#ffffff"`, `"#0d0f14"`). `None` = transparent. |
| `embed_css` | `bool` | `True` | Embed `<style>` block with per-layer CSS classes and colors. |
| `symbol_mode` | `bool` | `False` | Wrap geometry in `<symbol id="...">` instead of root `<svg>`. Used by `symbol_library()`. |
| `symbol_id` | `str` | `"symbol"` | XML `id` for the `<symbol>` element. Used when `symbol_mode=True`. |
| `font_family` | `str` | `"monospace"` | CSS `font-family` for `<text>` elements. |
| `max_text_height_fraction` | `Optional[float]` | `0.15` | Cap `font-size` at this fraction of the geometry's shorter viewBox dimension. Prevents oversized DXF text from obscuring symbol geometry. `None` = no cap (exact DXF height). |

### Output Size

When `target_width` / `target_height` are both `None`, the SVG `width` and `height` attributes are expressed in **physical inches** (e.g. `width="2.5in" height="1.0in"`), matching the DXF coordinate space 1:1. Any SVG viewer renders the symbol at its true physical size; browser JS that needs screen pixels normalises the `in` value itself.

When `target_width` or `target_height` is set, those values are used as plain px dimensions and the other axis is derived from the aspect ratio.

The `viewBox` always reflects the raw DXF coordinate space regardless of output size mode.

Lineweight floor: all strokes are floored at **0.25 mm** before the mm→px conversion (`96 px/in ÷ 25.4 mm/in = 3.78 px/mm`), preventing sub-pixel hairlines. `stroke_scale` multiplies the result.

---

## 4. Architecture — Data Flow

### File Layout

```
dxf2svg/
├── __init__.py          Package root — exports DXFConverter, BuildConfig
├── __main__.py          python -m dxf2svg entry point → cli.main()
├── converter.py         High-level pipeline orchestrator
├── cli.py               CLI: convert / audit / list / block / symbols
├── server.py            Flask bridge: file upload → engine → SVG JSON response
├── core/
│   ├── __init__.py
│   ├── extractor.py     DXF entity extraction + transform accumulation
│   └── svg_builder.py   Geometry → SVG: viewBox, CSS, bezier splines, Y-flip
└── ui/
    └── index.html       Dark engineering web UI
```

### Pipeline

```
Input .dxf file
        │
        ▼  DXFExtractor.__init__(dxf_path)
        │  Opens file via ezdxf; reads layer table → dict[name → LayerInfo]
        │
        ▼  DXFExtractor.extract(block_name) | extract_block(block_name)
        │  Recursively walks INSERT entities using Matrix44 transform stack
        │  Each INSERT: local_m = ins.matrix44() (handles base_point, rotation, scale)
        │  Compound: local_m @ parent_m  (row-vector convention — child→parent first)
        │  Converts each DXF entity → typed ExtXxx dataclass
        │  Cycle guard: set of visited block names prevents infinite recursion
        │  Yields: Iterator[ExtLine | ExtCircle | ExtArc | ...]
        │
        ▼  SVGBuilder(layers, config)
        │  Consumes entity iterator; computes bounding box with 5% padding
        │  Applies Y-flip via group transform: scale(1,-1) translate(0, ...)
        │  Maps DXF layer names → CSS class names (sanitizes spaces and slashes)
        │  Maps ACI color indices → hex RGB via lookup table
        │  Renders each entity as SVG element with stroke/fill from layer
        │  Normalizes output width/height to screen pixels
        │
        ▼  SVGBuilder.build() → str
           Returns formatted XML string (<?xml?> + <svg>...</svg>)
```

### ACI Color Mapping

DXF uses AutoCAD Color Index (ACI) integers for layer colors. `svg_builder.py` maps a subset of common ACI values to hex RGB:

| ACI | Color |
|-----|-------|
| 1 | `#FF0000` (red) |
| 2 | `#FFFF00` (yellow) |
| 3 | `#00FF00` (green) |
| 4 | `#00FFFF` (cyan) |
| 5 | `#0000FF` (blue) |
| 6 | `#FF00FF` (magenta) |
| 7 | `#000000` (black/white) |
| 250–255 | Grey scale |
| …others | See `ACI_COLORS` dict in `svg_builder.py` |

Unmapped ACI values fall back to `DEFAULT_STROKE = "#1a1a2e"` (dark navy).

---

## 5. Python API Reference

### DXFConverter

```python
from dxf2svg import DXFConverter, BuildConfig

conv = DXFConverter(dxf_path, unfold_all_layers=True, log_level=logging.INFO)
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `dxf_path` | `str` | required | Absolute or relative path to the `.dxf` file |
| `unfold_all_layers` | `bool` | `True` | Traverse frozen/locked/off layers. Set `False` to respect layer visibility. |
| `log_level` | `int` | `logging.INFO` | Python logging level |

---

#### `full_drawing(output_path, config=None) → str`

Converts the entire `*Model_Space` block to a single SVG file.

```python
svg_str = conv.full_drawing("panel.svg")
svg_str = conv.full_drawing("panel.svg", config=BuildConfig(background="#ffffff"))
```

Returns the SVG string. Also writes the file to `output_path`.

---

#### `block_to_svg(block_name, output_path, config=None) → str`

Converts a single named block to a standalone SVG file.

```python
svg_str = conv.block_to_svg("CIRCUIT_BREAKER", "breaker.svg")
```

Raises `KeyError` if `block_name` is not found in the DXF.

---

#### `symbol_library(output_dir, blocks=None, config=None, also_write_combined=True) → dict[str, str]`

Converts each named block to an individual SVG file, and optionally writes a combined `symbol_library.svg` with all blocks as `<symbol>` elements.

```python
results = conv.symbol_library(
    "symbols/",
    config=BuildConfig(symbol_mode=True, flip_y=True),
)
# results = {"CIRCUIT_BREAKER": "<svg>...", "TERMINAL": "<svg>..."}
```

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `output_dir` | `str` | required | Directory to write individual SVG files |
| `blocks` | `List[str] \| None` | `None` | Block names to process. `None` = all named blocks |
| `config` | `BuildConfig \| None` | `None` | Uses `symbol_mode=True` by default for each block |
| `also_write_combined` | `bool` | `True` | Write `symbol_library.svg` with all `<symbol>` elements |

Returns `dict[block_name → svg_string]`. Blocks with no renderable geometry are skipped with a warning.

The combined `symbol_library.svg` can be referenced via:
```html
<use href="symbol_library.svg#CIRCUIT_BREAKER" width="48" height="48"/>
```

---

#### `audit(pretty=True) → str`

Returns a JSON report of block structure and layer info.

```python
print(conv.audit())
```

```json
{
  "file": "panel.dxf",
  "blocks": {
    "*Model_Space": {"INSERT": 4, "MTEXT": 1},
    "CIRCUIT_BREAKER": {"ARC": 2, "INSERT": 2},
    "TERMINAL": {"CIRCLE": 2}
  },
  "layers": {
    "0": {
      "color_index": 7,
      "rgb": null,
      "linetype": "Continuous",
      "lineweight_mm": 0.25
    }
  }
}
```

---

#### `list_blocks() → List[str]`

Returns all named blocks (excludes `*Model_Space`, `*Paper_Space`, and other system blocks starting with `*`).

---

### DXFExtractor (internal)

`DXFExtractor` is instantiated by `DXFConverter`. It can also be used directly:

```python
from dxf2svg.core.extractor import DXFExtractor

ext = DXFExtractor("drawing.dxf", unfold_all_layers=True)
for entity in ext.extract("*Model_Space"):
    print(type(entity).__name__, entity.layer)
```

| Attribute | Type | Description |
|-----------|------|-------------|
| `ext.layers` | `dict[str, LayerInfo]` | All layers found in the DXF |
| `ext.audit` | `dict[str, dict]` | Entity type counts per block (populated after `extract()`) |

---

## 6. CLI Reference

```bash
python -m dxf2svg <command> <dxf_file> [options]
```

### Commands

#### `convert` — Full drawing to SVG

```bash
python -m dxf2svg convert drawing.dxf -o output.svg
python -m dxf2svg convert drawing.dxf -o output.svg --background "#ffffff" --stroke-scale 1.5
```

| Flag | Default | Description |
|------|---------|-------------|
| `-o, --output` | `<input>.svg` | Output SVG file path |
| `--no-flip-y` | off | Disable Y-axis flip |
| `--stroke-scale FLOAT` | `1.0` | Stroke width multiplier |
| `--width FLOAT` | auto | Target SVG width (px) |
| `--height FLOAT` | auto | Target SVG height (px) |
| `--background COLOR` | transparent | Background fill (e.g. `#ffffff`) |
| `--no-css` | off | Omit embedded CSS `<style>` block |
| `--show-frozen` | off | Respect frozen/off layers (default: unfold all) |
| `-v, --verbose` | off | Debug-level logging |

---

#### `block` — Single block to SVG

```bash
python -m dxf2svg block drawing.dxf CIRCUIT_BREAKER -o cb.svg
```

Same flags as `convert`. Requires `block_name` positional argument.

---

#### `symbols` — All blocks to SVG library

```bash
python -m dxf2svg symbols drawing.dxf -o ./symbols/
```

Writes one SVG per block + combined `symbol_library.svg` to the output directory. Same flags as `convert`.

---

#### `audit` — Diagnose DXF structure

```bash
python -m dxf2svg audit drawing.dxf
```

Prints JSON block/entity/layer report to stdout. Run before conversion to understand what's in the DXF.

---

#### `list` — List named blocks

```bash
python -m dxf2svg list drawing.dxf
```

Prints all named blocks (one per line, alphabetical). Use to identify block names before using `block` or `symbols` commands.

---

## 7. Web API Reference

The Flask server (`server.py`) exposes two endpoints. It is a thin bridge only — all conversion logic runs in the Python engine.

### `POST /api/convert`

Converts an uploaded DXF file.

**Request:** `multipart/form-data`

**Upload constraints:** max 50 MB; filename must end in `.dxf`; file content must be plain-text ASCII/UTF-8 (binary blobs are rejected with HTTP 400).

| Field | Type | Description |
|-------|------|-------------|
| `file` | file | The `.dxf` file to convert (max 50 MB) |
| `options` | JSON string | Conversion options (see below) |

**Options JSON fields:**

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `mode` | `"full"` \| `"block"` \| `"symbols"` | `"full"` | Conversion mode |
| `block` | `str` | — | Block name (required when `mode="block"`) |
| `flipY` | `bool` | `true` | Flip Y-axis |
| `unfold` | `bool` | `true` | Traverse frozen/locked layers |
| `embedCSS` | `bool` | `true` | Embed CSS layer styles |
| `symbolMode` | `bool` | `false` | Output `<symbol>` wrapper |
| `strokeScale` | `float` | `1.0` | Stroke width multiplier |
| `background` | `str \| null` | `"white"` | Background color hex (`null` = transparent) |

**Response:** `application/json`

```json
{
  "svg": "<svg xmlns=...>...</svg>",
  "entity_count": 47,
  "raw_extents": { "width_in": 2.5, "height_in": 1.0 },
  "audit": {
    "file": "drawing.dxf",
    "blocks": { ... },
    "layers": { ... }
  },
  "layers": {
    "0": { "rgb": null, "linetype": "Continuous", "lineweight_mm": 0.25 },
    "Defpoints": { "rgb": [255, 0, 0], "linetype": "Continuous", "lineweight_mm": 0.25 }
  }
}
```

**Error response:**
```json
{ "error": "No file uploaded" }
```

---

### `POST /api/blocks`

Returns the list of named blocks without converting.

**Request:** `multipart/form-data` — `file` field only (no options needed).

**Response:**

```json
{ "blocks": ["CIRCUIT_BREAKER", "TERMINAL", "METER"] }
```

---

## 8. Web UI

**Entry:** `python dxf2svg/server.py` → `http://localhost:5000`

### Features

**Left panel — Input & Configuration**
- Drag-drop or click-to-browse DXF file upload
- Conversion mode selector: Full Drawing / Single Block / Symbol Library
- Block selector (populated from DXF after file load, before conversion)
- Options: Flip Y, Unfold layers, Embed CSS, Symbol mode, Stroke scale, Background color
- Convert / Save SVG / Copy SVG Code buttons

**Center panel — Preview**
- Live SVG preview after conversion
- Zoom in/out with +/− buttons and mouse wheel
- Fit-to-window button
- Checkerboard background toggle
- **EDIT mode** (toggle via EDIT button):

  *Selection & movement*
  - Select tool (↖): click any SVG element to select it; dashed overlay shows bounds
  - Move tool (⤢): click then drag selected element to reposition

  *Draw tools*
  - Line (╱): drag to draw a line segment
  - Circle (○): drag from center to set radius
  - Rectangle (□): drag corner-to-corner
  - Text (T+): click in canvas to place text (prompted); `T×` strips all `<text>` elements

  *Transform tools (Xform group)*
  - Rotate (↻): drag element to rotate around its bounding box center; key `R` toggles the tool
  - Flip H (↔): mirror element horizontally around center; key `H`
  - Flip V (↕): mirror element vertically around center; key `V`
  - Duplicate (⧉): clone element with small diagonal offset, auto-selects the clone; `Ctrl+D`

  *Appearance controls* (props bar, shown when element selected)
  - Stroke color picker — pre-populated from element's current `stroke` attribute
  - Stroke width input — pre-populated from element's current `stroke-width`
  - Fill color picker — pre-populated from element's current `fill` attribute
  - "None" — sets `fill="none"`; "Apply" — writes all three to the element

  *In-place block drilling*
  - Double-click any `<g>` group to enter its context; blue outline marks the active group
  - Breadcrumb bar appears showing the nesting path (e.g. `root › PANEL › CB`)
  - All selection/editing is scoped to children of the active group only
  - "↑ Exit Block" button or `Escape` to pop one level; nested drilling is fully supported

  *Undo/Delete*
  - Delete (✕), `Delete` or `Backspace` key
  - Undo (↩) `Ctrl+Z`, Redo (↪) `Ctrl+Y` — 60-step stack; all operations are undoable

  *Properties bar*
  - Element tag and info (radius, length, text preview, child count)
  - Scale: ½× ¾× 1.5× 2× + custom numeric; Appearance controls (above); Delete

  *Export*
  - Export edited SVG (↓ SVG) — strips selection overlay, opens native OS Save As dialog

**Right panel — Diagnostics**
- Block audit tree: entity counts per block after conversion
- Layer list: color swatches, names, linetypes
- Log: timestamped conversion and error messages

**Save behavior**
- "Save SVG" uses the **File System Access API** (`showSaveFilePicker`) on supported browsers (Chrome, Edge) to open a native OS file explorer dialog for choosing save location and filename.
- Falls back to browser-managed download on Firefox/Safari.

---

## 9. Integration with ELiGen

ELiGen's symbol importer uses dxf2svg to render SVG thumbnails for blocks in the Symbol Library Admin UI.

**Import path from ELiGen:**

```python
from dxf2svg import DXFConverter, BuildConfig
```

The parent directory (`G:\`) must be on `sys.path`, or dxf2svg must be installed as a package.

**Usage pattern in ELiGen (`symbol_importer/renderer.py`):**

```python
conv = DXFConverter(dxf_path)
results = conv.symbol_library(
    output_dir=thumb_dir,
    config=BuildConfig(
        flip_y=True,
        symbol_mode=True,
        embed_css=True,
        background=None,    # transparent thumbnails
    ),
)
# results[block_name] = svg_string stored in eligen.db symbols table
```

**Important:** Never cache `DXFConverter` instances across requests — they hold open file handles via ezdxf. Create a new instance per conversion.

---

## 10. Known Limitations

| Limitation | Detail |
|-----------|--------|
| 2D only | Z-coordinates are read but ignored. 3D geometry is projected flat. |
| Complex linetypes | Dashed, dotted, and symbol linetypes are rendered as solid strokes. |
| HATCH fill | Only the boundary polyline is extracted. Fill patterns are not rendered. |
| SPLINE accuracy | `flattening(0.01)` produces ≤ 0.01 DXF-unit deviation. Very high curvature splines may generate many segments. Falls back to control points if `flattening()` is unavailable. |
| MTEXT rich formatting | Bold, italic, color overrides, and embedded fields in MTEXT are stripped to plain text. |
| Attributed INSERTs | ATTRIB entities are extracted only when they behave like standard TEXT entities. |
| Linked XREFs | External references (XREFs) are not resolved. |
| MTEXT entity color | MTEXT color overrides are stripped with rich formatting; only the entity-level `true_color`/ACI is preserved. |
| MTEXT API (ezdxf 1.x) | `plain_mtext()` was removed in ezdxf 1.0. The extractor calls `plain_text()` with an `AttributeError` fallback (`getattr(e.dxf, "text", "")`) for any build variation. |

---

## 11. Key Conventions

1. **All internal imports are relative.** `converter.py`, `cli.py`, and core modules use `.core.extractor`, `.core.svg_builder`, `.converter`, etc. `server.py` is the only exception — it runs as a standalone script and uses `sys.path` manipulation to import `dxf2svg.*`.

2. **No side effects on import.** `DXFExtractor` opens the DXF file in `__init__`, not lazily. Do not create `DXFConverter` instances at module level.

3. **Y-flip is always on.** `flip_y=True` is the default and should never be disabled for visual output. DXF origin is bottom-left; SVG origin is top-left.

4. **Output size is 1:1 physical inches.** When `target_width`/`target_height` are both `None`, the SVG `width` and `height` are expressed in physical inch units (e.g. `width="2.5in"`), matching DXF coordinates 1:1. Raw DXF coordinates become SVG coordinates directly — no pixel normalisation is applied. JS display code that needs screen pixels normalises the `in` value itself (`showPreview()` in the web UI normalises to 800 px for the preview workspace).

5. **Symbol mode.** `symbol_mode=True` wraps all geometry in `<symbol id="...">` for use in `<defs>`. The combined `symbol_library.svg` produced by `symbol_library()` is a hidden `<svg><defs>` container referenced via `<use href="...#id">`.

6. **Stroke widths.** Computed as `max(MIN_LW_MM, lineweight_mm) × MM_TO_PX × stroke_scale`, where `MIN_LW_MM = 0.25` mm and `MM_TO_PX = 96 / 25.4 ≈ 3.7795 px/mm`. This floors all strokes at **0.25 mm (0.945 px)**, preventing sub-pixel hairlines. The `vector-effect: non-scaling-stroke` CSS property is always applied, so strokes render at a consistent visual weight regardless of zoom.

7. **Layer name sanitization.** Layer names are sanitized for CSS class names: spaces and slashes become underscores. Class names take the form `layer-NAME`.

8. **Cycle guard.** Block traversal uses an add-before/discard-after pattern (block added to `_visited_blocks` before recursing, discarded after returning). This correctly prevents infinite cycles in self-referencing blocks while still allowing the **same block to appear at multiple INSERT positions** (sequential, not nested). `*Model_Space` is deliberately excluded from the guard.

9. **INSERT transform math.** `ins.matrix44()` (ezdxf built-in) is used for all INSERT transforms — it correctly subtracts the block `base_point` from the translation component, applies rotation, and scales. Manual matrix construction must not be used. ezdxf uses **row-vector convention** (`m.transform(v) = v @ m`), so compound matrices are composed as `local_m @ parent_m` (child→parent first), not `parent_m @ local_m`. The two are only equivalent for pure translations; any rotation component makes the order critical.

10. **Arc angle convention.** `ExtArc.start_angle` and `end_angle` are in world-space degrees after all parent transforms are applied. The rotation contribution from `_rotation_from_matrix(m)` is added to the block-local angles. In ezdxf's row-vector rotation matrix, `m[1,0] = −sin θ`; the extractor negates this to correctly recover `sin θ` → `atan2(sin, cos) = +θ`.

11. **Entity-level color resolution.** `_entity_rgb(entity)` returns `(R, G, B)` or `None` using AutoCAD priority order: (1) `true_color` group code 420 — 24-bit explicit RGB, always wins; (2) `color` group code 62 ACI 1–255 — looked up in `_ACI_TABLE` (full `DXF_DEFAULT_COLORS` covering all 255 ACI values, normalised to dict at import time); (3) 256/BYLAYER or 0/BYBLOCK → return `None` (caller uses layer colour). LWPOLYLINE/POLYLINE propagate the parent entity's color to their virtual LINE/ARC children via a `color_override` parameter. In `SVGBuilder._apply_attrs()`, a non-`None` color adds an inline `stroke="#{RR}{GG}{BB}"` that overrides the layer CSS class stroke. **ACI 7 special case:** ezdxf's built-in table stores ACI 7 as `0xFFFFFF` (white — AutoCAD dark-background convention). `_ACI_TABLE[7]` is patched to `0x000000` (black) at import time so that entities and layers with the "default" ACI color render visibly on SVG's white canvas instead of appearing as invisible white shapes that cover underlying geometry.

12. **Text alignment.** `ExtText` carries `h_align` (DXF group code 72) and `v_align` (group code 73). For center (1), right (2), aligned (3), middle (4), or fit (5) horizontal alignment, the visual anchor is `align_point` (group code 11), not `insert` (group code 10) — the extractor switches to `align_point` for these modes. `SVGBuilder` maps `h_align` → SVG `text-anchor` (`middle` for 1/4, `end` for 2; `start` is the SVG default and is not emitted). `v_align` → `dominant-baseline` (`central` for middle=2, `hanging` for top=3; baseline/bottom use the SVG auto default).

13. **Text height capping.** The SVG viewBox is determined by **non-text geometry only** (lines, circles, arcs, polylines). Text anchor points are tracked separately and only used as the viewBox fallback when no geometry exists. Then, `SVGBuilder._svg_text()` caps `font-size` at `min(char_height, shorter_padded_dim × max_text_height_fraction)`. The default fraction is **0.15** (15%), controlled by `BuildConfig.max_text_height_fraction` (`None` disables the cap entirely). This prevents DXF text that was designed for a full-drawing context (large char_height relative to the block's geometric footprint) from creating giant glyph shapes that obscure the symbol geometry.

14. **Multi-line MTEXT rendering.** When `ExtText.text` contains newlines, `SVGBuilder._svg_text()` emits `<tspan>` child elements — one per line — instead of setting `el.text` directly. Each `<tspan>` gets `x=` (same as parent anchor) and `dy="0"` for the first line / `dy="1.5em"` for subsequent lines (approximates DXF MTEXT default line spacing of 1.667×). Empty lines receive a non-breaking space (`\u00a0`) so they preserve vertical rhythm. Single-line text is set as `el.text` directly (no `<tspan>` wrapper).

15. **`<text>` elements omit CSS layer class.** `SVGBuilder._svg_text()` does **not** set `class=` on `<text>` elements. CSS class selectors have higher specificity than presentation attributes, so a `.layer-0 { stroke: #000000 }` rule would override `stroke="none"` and paint unwanted outlines on every glyph. By omitting the class, the inline `fill=` and `stroke="none"` presentation attributes are the final authority on text colour. Lines, circles, arcs, and other geometry elements continue to carry `class="layer-NAME"` normally.

---

## 12. Running dxf2svg

### Install dependencies

**Recommended — venv (avoids Windows site-packages path issues):**

```bash
cd G:/dxf2svg
python -m venv .venv
.venv\Scripts\pip install ezdxf flask
```

**Or system-wide:**
```bash
pip install ezdxf flask
```

### CLI

```bash
# From G:\ (dxf2svg on sys.path as a package)
python -m dxf2svg audit    drawing.dxf
python -m dxf2svg list     drawing.dxf
python -m dxf2svg convert  drawing.dxf -o output.svg
python -m dxf2svg block    drawing.dxf CIRCUIT_BREAKER -o cb.svg
python -m dxf2svg symbols  drawing.dxf -o ./symbols/
```

### Web UI

```bash
# Using venv
G:/dxf2svg/.venv/Scripts/python.exe G:/dxf2svg/server.py

# Or via serve.cmd (sets correct Python path automatically)
G:/dxf2svg/serve.cmd
# → http://localhost:5000
```

### Python API

```python
import sys
sys.path.insert(0, "G:/")      # only needed if not installed as package

from dxf2svg import DXFConverter, BuildConfig

# Full drawing
conv = DXFConverter("panel.dxf")
conv.full_drawing("panel.svg")

# Single block
conv.block_to_svg("CIRCUIT_BREAKER", "cb.svg", config=BuildConfig(background="#ffffff"))

# Symbol library for ELiGen
results = conv.symbol_library(
    "symbols/",
    config=BuildConfig(flip_y=True, symbol_mode=True),
)
```

### Tests

```bash
# Run full test suite (35 tests)
G:/dxf2svg/.venv/Scripts/pytest tests/ -v
```

**Test files:**

| File | Coverage |
|------|----------|
| `tests/test_insert_placement.py` | INSERT base_point alignment, nested inserts, rotation compound matrix, arc angles in rotated blocks, same-block multi-insert (cycle guard regression) |
| `tests/test_buildconfig.py` | `preserve_size` field removed, default field values |
| `tests/test_server.py` | Upload size cap (50 MB), debug mode off, DXF extension + binary-content validation, 413 handler |
| `tests/test_entity_colors.py` | ACI color override → RGB; true_color override; BYLAYER → `None`; true_color beats ACI; entity color in SVG stroke; BYLAYER uses layer color; LWPOLYLINE color forwarded to children; center/right text uses align_point; h_align/v_align stored; SVG text-anchor middle/end; left text no text-anchor; text has no CSS class; ACI 7 renders dark (not white) on SVG canvas; text entity color in SVG fill; MTEXT renders as `<text>` element; MTEXT multi-line uses `<tspan>`; MTEXT extraction produces ExtText |
