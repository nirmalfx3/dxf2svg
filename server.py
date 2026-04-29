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
from werkzeug.exceptions import HTTPException

# Add parent dir to path when running from dxf2svg/ subfolder
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dxf2svg.converter import DXFConverter
from dxf2svg.core.svg_builder import BuildConfig

app = Flask(__name__, static_folder="ui", static_url_path="")
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50 MB — DX-SEC-002


@app.errorhandler(413)
def payload_too_large(e):
    return jsonify({"error": "File too large — maximum upload size is 50 MB"}), 413


def _validate_dxf_upload(file):
    """
    Validate an uploaded file as a DXF.  Returns (error_msg, status_code) on
    failure, or (None, None) on success.  DX-SEC-003.
    """
    fname = (file.filename or "").lower()
    if not fname.endswith(".dxf"):
        return "Only .dxf files are accepted", 400

    # Sniff first 16 bytes — DXF is plain ASCII/UTF-8 text.
    # Binary files (JPEG, ZIP, PNG, ELF …) have bytes outside printable ASCII.
    header = file.read(16)
    file.seek(0)
    check = header[3:] if header.startswith(b'\xef\xbb\xbf') else header  # skip UTF-8 BOM
    if check and not all(
        b == 0x09 or b == 0x0A or b == 0x0D or (0x20 <= b <= 0x7E)
        for b in check
    ):
        return "File does not appear to be a DXF (non-text content detected)", 400

    return None, None


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

        # Validate extension + binary content — DX-SEC-003
        err, code = _validate_dxf_upload(file)
        if err:
            return jsonify({"error": err}), code

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

    except HTTPException:
        raise  # let Flask's error handlers (e.g. 413) deal with HTTP exceptions
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
        err, code = _validate_dxf_upload(file)
        if err:
            return jsonify({"error": err}), code
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp_path = tmp.name
            file.save(tmp_path)
        try:
            conv = DXFConverter(tmp_path)
            blocks = conv.list_blocks()
            return jsonify({"blocks": blocks})
        finally:
            os.unlink(tmp_path)
    except HTTPException:
        raise
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    # DX-FH-005: configure root logger here (entry-point), not inside
    # DXFConverter.__init__.  This keeps embedding apps (ELiGen) in control
    # of their own logging setup.
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="[%(levelname)s] %(name)s: %(message)s",
    )
    print("\n  DXF -> SVG Converter")
    print("  ---------------------")
    print("  Open:  http://localhost:5000")
    print("  Stop:  Ctrl+C\n")
    app.run(debug=os.environ.get("FLASK_DEBUG", "").lower() == "true", port=5000)  # DX-SEC-001
