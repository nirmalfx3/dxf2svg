# dxf2svg — Developer Reference

> **Suite tool:** Utility library used by ELiGen for DXF block thumbnail generation. Also usable standalone via CLI or web UI.

---

## Suite Wiki

dxf2svg is part of the **Solar PV Software Suite**. A central knowledge base lives at:

**`G:\suite-wiki\`** — read `index.md` first to navigate.

The wiki holds cross-project context: how ELiGen uses dxf2svg, the full integration topology, shared conventions, and patterns for extending the suite.

| Situation | Page to read |
|-----------|-------------|
| How ELiGen uses dxf2svg | `suite/integration-topology.md` |
| How dxf2svg fits into the suite | `projects/dxf2svg.md` |
| Adding a new tool or interface | `patterns/new-suite-tool.md` |
| Running or interpreting a framework audit | `patterns/framework-audit.md` + `audits/dxf2svg.md` |

When dxf2svg changes its Python API or supported entities, update `projects/dxf2svg.md` in place.

---

## What It Does

Converts AutoCAD DXF files to clean, normalized SVG output. Handles nested block hierarchies, full matrix transform chains, and 10+ DXF entity types. Three interfaces: CLI, Flask web UI, and Python API.

---

## Tech Stack

| Layer | Choice |
|-------|--------|
| Language | Python 3.11+ |
| DXF parsing | ezdxf ≥ 1.3 |
| Web server | Flask ≥ 3.0 |
| SVG generation | stdlib `xml.etree.ElementTree` |
| Data classes | stdlib `dataclasses` |

**Deviation from suite default:** Uses Flask (not FastAPI) because this is a thin file-upload bridge, not a structured API. No Pydantic models needed — all configuration is via `BuildConfig` dataclass.

---

## Architecture

```
dxf2svg/
├── __init__.py          Package root — exports DXFConverter, BuildConfig
├── __main__.py          python -m dxf2svg entry point → cli.main()
├── converter.py         High-level pipeline: full_drawing / block_to_svg / symbol_library / audit
├── cli.py               CLI: convert / audit / list / block / symbols commands
├── server.py            Flask bridge: file upload → engine → SVG JSON response
├── core/
│   ├── __init__.py
│   ├── extractor.py     DXF entity extraction: walks block hierarchy, accumulates transforms
│   └── svg_builder.py   Geometry → SVG: viewBox, CSS layers, bezier splines, Y-flip
└── ui/
    └── index.html       Dark engineering UI: drag-drop upload, live preview, zoom, layer panel
```

---

## Data Flow

```
Input DXF file
      │
      ▼ DXFExtractor (core/extractor.py)
      │   - Recursively traverses nested INSERT blocks
      │   - Accumulates Matrix44 transform chain
      │   - Yields normalized geometry objects (ExtLine, ExtCircle, ExtArc, …)
      │
      ▼ SVGBuilder (core/svg_builder.py)
      │   - Computes bounding box + viewBox
      │   - Applies Y-axis flip (DXF bottom-left → SVG top-left)
      │   - Maps DXF layers → CSS classes with ACI color fallback
      │   - Outputs formatted XML SVG string
      │
Output SVG file / string
```

---

## Usage

```bash
# Install
pip install ezdxf flask

# CLI
python -m dxf2svg convert drawing.dxf -o output.svg
python -m dxf2svg audit   drawing.dxf
python -m dxf2svg list    drawing.dxf
python -m dxf2svg block   drawing.dxf CIRCUIT_BREAKER -o cb.svg
python -m dxf2svg symbols drawing.dxf -o ./symbols/

# Web UI (http://localhost:5000)
python dxf2svg/server.py

# Python API
from dxf2svg import DXFConverter, BuildConfig
conv = DXFConverter("panel.dxf")
conv.full_drawing("panel.svg")
conv.symbol_library("symbols/", config=BuildConfig(flip_y=True))
```

---

## Integration with ELiGen

ELiGen uses `DXFConverter.symbol_library()` to generate SVG thumbnails for the Symbol Library Admin UI. Import path from ELiGen:

```python
from dxf2svg import DXFConverter, BuildConfig
```

The parent directory (`G:\`) must be on `sys.path`, or dxf2svg must be installed as a package.

---

## Key Conventions

1. **Imports:** All internal imports are relative (`.core.extractor`, `.converter`). Never use absolute `dxf2svg.` imports inside the package itself — `server.py` is the only exception (it runs as a script with `sys.path` manipulation).
2. **No side effects on import:** `DXFExtractor` opens the DXF file in `__init__`, not on method call. Avoid creating `DXFConverter` instances at module level.
3. **Symbol mode:** `BuildConfig(symbol_mode=True, symbol_id="BLOCK_NAME")` wraps geometry in a `<symbol>` element instead of a root `<svg>`. Used by `symbol_library()`.
4. **Y-flip default:** `flip_y=True` in `BuildConfig` — DXF uses bottom-left origin, SVG uses top-left. Always flip for visual correctness.
5. **Frozen layers:** `unfold_all_layers=True` (default) traverses layers regardless of frozen/locked state, needed for symbol extraction.

---

## What NOT to Do

- Do not add FastAPI or Pydantic — the Flask bridge is intentionally minimal.
- Do not render 3D geometry (Z coordinates are ignored; this is a 2D converter).
- Do not cache `DXFConverter` instances — they hold open file handles via ezdxf.
- Do not change the `server.py` static folder path (`ui/`) or the `ui/index.html` location — the Flask app is configured to serve from `ui/`.
