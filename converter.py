"""
dxf2svg/converter.py
─────────────────────
High-level conversion pipeline.

Modes:
  1. full_drawing()   → convert entire model space → single SVG
  2. symbol_library() → convert each named block → <symbol> in a shared SVG defs library
  3. block_to_svg()   → convert a single named block → standalone SVG
  4. audit()          → return JSON-friendly dict of block structure for diagnostics
"""

import json
import logging
import os
from typing import Optional, Dict, List

from .core.extractor import DXFExtractor
from .core.svg_builder import SVGBuilder, BuildConfig

logger = logging.getLogger(__name__)


class DXFConverter:
    """
    Main entry point for DXF → SVG conversion.

    Example:
        conv = DXFConverter("panel.dxf")
        conv.full_drawing("panel.svg")
        conv.symbol_library("symbols/")
    """

    def __init__(
        self,
        dxf_path: str,
        unfold_all_layers: bool = True,
        log_level: int = logging.INFO,
    ):
        logging.basicConfig(
            level=log_level,
            format="[%(levelname)s] %(name)s: %(message)s"
        )
        self.dxf_path = dxf_path
        self.extractor = DXFExtractor(dxf_path, unfold_all_layers=unfold_all_layers)
        self.last_entity_count: int = 0

    # ── public API ────────────────────────────────────────────────────────────

    def full_drawing(
        self,
        output_path: str,
        config: Optional[BuildConfig] = None,
    ) -> str:
        """
        Convert the entire model space to a single SVG file.
        Returns the SVG string.
        """
        cfg = config or BuildConfig()
        logger.info("Extracting model space…")
        entities = list(self.extractor.extract("*Model_Space"))
        logger.info(f"  → {len(entities)} geometry objects extracted")

        builder = SVGBuilder(self.extractor.layers, cfg)
        builder.add_entities(entities)
        svg_str = builder.build()
        self.last_entity_count = builder.entity_count

        _write(output_path, svg_str)
        logger.info(f"Saved: {output_path}")
        return svg_str

    def block_to_svg(
        self,
        block_name: str,
        output_path: str,
        config: Optional[BuildConfig] = None,
    ) -> str:
        """
        Convert a single named block to a standalone SVG file.
        Returns the SVG string.
        """
        cfg = config or BuildConfig()
        logger.info(f"Extracting block: '{block_name}'…")
        entities = list(self.extractor.extract_block(block_name))
        logger.info(f"  → {len(entities)} geometry objects")

        builder = SVGBuilder(self.extractor.layers, cfg)
        builder.add_entities(entities)
        svg_str = builder.build()
        self.last_entity_count = builder.entity_count

        _write(output_path, svg_str)
        logger.info(f"Saved: {output_path}")
        return svg_str

    def symbol_library(
        self,
        output_dir: str,
        blocks: Optional[List[str]] = None,
        config: Optional[BuildConfig] = None,
        also_write_combined: bool = True,
    ) -> Dict[str, str]:
        """
        Convert each named block to a separate SVG file.
        Also writes a combined symbol_library.svg with all as <symbol> elements.

        Returns dict of {block_name: svg_string}.
        """
        os.makedirs(output_dir, exist_ok=True)
        target_blocks = blocks or self.extractor.list_blocks()
        results = {}

        for bname in target_blocks:
            cfg = config or BuildConfig(symbol_mode=True, symbol_id=_safe_id(bname))
            entities = list(self.extractor.extract_block(bname))
            if not entities:
                logger.warning(f"  Block '{bname}' has no renderable geometry — skipped")
                continue

            builder = SVGBuilder(self.extractor.layers, cfg)
            builder.add_entities(entities)
            svg_str = builder.build()

            fname = os.path.join(output_dir, f"{_safe_id(bname)}.svg")
            _write(fname, svg_str)
            results[bname] = svg_str
            logger.info(f"  [{bname}] → {fname}")

        if also_write_combined and results:
            combined = _build_combined_library(results, self.extractor.layers)
            lib_path = os.path.join(output_dir, "symbol_library.svg")
            _write(lib_path, combined)
            logger.info(f"Combined library → {lib_path}")

        return results

    def audit(self, pretty: bool = True) -> str:
        """
        Return a JSON report of block structure, entity counts, and layer info.
        Useful for diagnosing what's in the DXF before converting.
        """
        # Walk all blocks to populate audit data
        _ = list(self.extractor.extract("*Model_Space"))

        report = {
            "file": os.path.basename(self.dxf_path),
            "blocks": {},
            "layers": {},
        }

        for bname, counts in self.extractor.audit.items():
            report["blocks"][bname] = counts

        for lname, info in self.extractor.layers.items():
            report["layers"][lname] = {
                "color_index": info.color_index,
                "rgb": info.rgb,
                "linetype": info.linetype,
                "lineweight_mm": info.lineweight,
            }

        return json.dumps(report, indent=2 if pretty else None)

    def list_blocks(self) -> List[str]:
        return self.extractor.list_blocks()


# ── helpers ───────────────────────────────────────────────────────────────────

def _write(path: str, content: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _safe_id(name: str) -> str:
    """Convert block name to a safe filename / XML id."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _build_combined_library(results: dict, layers: dict) -> str:
    """
    Combine all individual symbol SVGs into a single <svg><defs> library.
    Consumers reference symbols via <use href="symbol_library.svg#BLOCK_NAME"/>
    """
    import xml.etree.ElementTree as ET
    root = ET.Element("svg")
    root.set("xmlns", "http://www.w3.org/2000/svg")
    root.set("xmlns:xlink", "http://www.w3.org/1999/xlink")
    root.set("style", "display:none")  # Library SVG is invisible, used via <use>

    defs = ET.SubElement(root, "defs")

    for bname, svg_str in results.items():
        try:
            # Parse individual symbol SVG, extract <symbol> element
            tree = ET.fromstring(svg_str)
            ns = {"svg": "http://www.w3.org/2000/svg"}
            sym = tree.find(".//svg:symbol", ns) or tree.find(".//symbol")
            if sym is not None:
                sym.set("id", _safe_id(bname))   # ensure consistent ID
                defs.append(sym)
        except Exception as e:
            logger.warning(f"Could not merge '{bname}' into library: {e}")

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
