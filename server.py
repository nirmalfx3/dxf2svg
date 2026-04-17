"""
server.py
─────────
Minimal Flask server bridging the HTML UI and the dxf2svg Python engine.

Install:  pip install flask ezdxf
Run:      python server.py
Open:     http://localhost:5000
"""

import os, json, tempfile, traceback
from flask import Flask, request, jsonify, send_from_directory

# Add parent dir to path when running from dxf2svg/ subfolder
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dxf2svg.converter import DXFConverter
from dxf2svg.core.svg_builder import BuildConfig

app = Flask(__name__, static_folder="ui", static_url_path="")


@app.route("/")
def index():
    return send_from_directory("ui", "index.html")


@app.route("/api/convert", methods=["POST"])
def convert():
    try:
        file = request.files.get("file")
        opts = json.loads(request.form.get("options", "{}"))

        if not file:
            return jsonify({"error": "No file uploaded"}), 400

        # Save to temp file
        suffix = ".dxf"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = tmp.name
            file.save(tmp_path)

        try:
            conv = DXFConverter(
                tmp_path,
                unfold_all_layers=opts.get("unfold", True),
            )

            cfg = BuildConfig(
                flip_y=opts.get("flipY", True),
                preserve_size=opts.get("preserveSize", False),
                embed_css=opts.get("embedCSS", True),
                symbol_mode=opts.get("symbolMode", False),
                stroke_scale=float(opts.get("strokeScale", 1.0)),
                background=opts.get("background") or None,
            )

            mode = opts.get("mode", "full")

            if mode == "full":
                svg_str = conv.full_drawing(tmp_path.replace(".dxf", ".svg"), config=cfg)
            elif mode == "block":
                block_name = opts.get("block")
                if not block_name:
                    return jsonify({"error": "No block selected"}), 400
                svg_str = conv.block_to_svg(
                    block_name,
                    tmp_path.replace(".dxf", ".svg"),
                    config=cfg
                )
            elif mode == "symbols":
                results = conv.symbol_library(
                    os.path.join(tempfile.gettempdir(), "dxf2svg_symbols"),
                    config=cfg,
                )
                # Return combined library as the SVG
                svg_str = "\n\n".join(results.values()) if results else "<svg/>"
            else:
                return jsonify({"error": f"Unknown mode: {mode}"}), 400

            entity_count = conv.last_entity_count
            raw_w, raw_h = conv.last_raw_extents if conv.last_raw_extents else (None, None)

            # Serialize audit and layer info
            audit_json = json.loads(conv.audit())

            return jsonify({
                "svg": svg_str,
                "entity_count": entity_count,
                "raw_extents": {"width_in": raw_w, "height_in": raw_h},
                "audit": audit_json,
                "layers": {
                    name: {
                        "rgb": info.rgb,
                        "linetype": info.linetype,
                        "lineweight_mm": info.lineweight,
                    }
                    for name, info in conv.extractor.layers.items()
                },
            })

        finally:
            os.unlink(tmp_path)
            svg_out = tmp_path.replace(".dxf", ".svg")
            if os.path.exists(svg_out):
                os.unlink(svg_out)

    except Exception as e:
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route("/api/blocks", methods=["POST"])
def list_blocks():
    """Quick block listing without full conversion."""
    try:
        file = request.files.get("file")
        if not file:
            return jsonify({"error": "No file"}), 400
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp_path = tmp.name
            file.save(tmp_path)
        try:
            conv = DXFConverter(tmp_path)
            blocks = conv.list_blocks()
            return jsonify({"blocks": blocks})
        finally:
            os.unlink(tmp_path)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    print("\n  DXF -> SVG Converter")
    print("  ---------------------")
    print("  Open:  http://localhost:5000")
    print("  Stop:  Ctrl+C\n")
    app.run(debug=True, port=5000)
