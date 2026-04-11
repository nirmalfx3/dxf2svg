# dxf2svg — DXF → SVG Converter

Preserves nested blocks, full transform chain, circles, arcs, splines,
layer metadata, and outputs clean normalized SVG.

## Installation

```bash
pip install ezdxf flask
pip install -e .         # install CLI tool
```

## Quick Start

### CLI
```bash
# Audit what's in the DXF (diagnose before converting)
python -m dxf2svg audit drawing.dxf

# List all named blocks
python -m dxf2svg list drawing.dxf

# Convert full drawing
python -m dxf2svg convert drawing.dxf -o output.svg

# Convert a single named block
python -m dxf2svg block drawing.dxf CIRCUIT_BREAKER -o breaker.svg

# Export all blocks as SVG symbol library
python -m dxf2svg symbols drawing.dxf -o ./symbols/
```

### Web UI
```bash
cd dxf2svg
python server.py
# Open http://localhost:5000
```

### Python API
```python
from dxf2svg import DXFConverter, BuildConfig

conv = DXFConverter("panel.dxf")

# Full drawing
conv.full_drawing("panel.svg")

# Single block with custom options
cfg = BuildConfig(
    flip_y=True,
    stroke_scale=1.5,
    background="#ffffff",
    symbol_mode=False,
)
conv.block_to_svg("CIRCUIT_BREAKER", "breaker.svg", config=cfg)

# Symbol library — one SVG per block + combined library SVG
conv.symbol_library("./symbols/", config=cfg)

# Audit block structure (returns JSON string)
print(conv.audit())
```

## What Gets Preserved

| DXF Entity | SVG Output | Notes |
|---|---|---|
| LINE | `<line>` | Full transform chain applied |
| CIRCLE | `<circle>` or `<ellipse>` | Non-uniform scale → ellipse |
| ARC | `<path>` A command | Start/end angles rotated by parent INSERT |
| LWPOLYLINE | `<polyline>/<polygon>` | |
| POLYLINE | `<polyline>/<polygon>` | |
| SPLINE | `<path>` C command | Catmull-Rom → cubic bezier |
| ELLIPSE | `<ellipse>` | Rotation preserved |
| TEXT/MTEXT | `<text>` | Height scaled, rotation applied |
| SOLID | `<polygon>` | Filled |
| HATCH | boundary `<polyline>` | Fill boundary only |
| **INSERT (nested)** | Recursively resolved | ✅ Full matrix chain |
| Layers | CSS `.layer-NAME` classes | ACI color → hex |

## Known Limitations

- OLE objects, raster images, and 3D solids skipped
- Complex linetypes rendered as solid (linetype dash pattern not emitted)
- Attributed INSERT text extracted only if ATTRIB is TEXT-compatible
- SPLINE degree > 3 approximated via Catmull-Rom

## Diagnosing Missing Geometry

Run the audit first:
```bash
python -m dxf2svg audit drawing.dxf
```

If CIRCLEs appear in the audit for a block but not in the SVG output,
it means they were on a frozen/locked layer. Re-run with `--show-frozen`
disabled (default behaviour already unfolds all layers).
