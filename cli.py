#!/usr/bin/env python3
"""
dxf2svg/cli.py
──────────────
Command-line interface for DXF → SVG conversion.

Usage examples:
  python -m dxf2svg convert drawing.dxf -o output.svg
  python -m dxf2svg audit   drawing.dxf
  python -m dxf2svg symbols drawing.dxf -o ./symbols/
  python -m dxf2svg block   drawing.dxf CIRCUIT_BREAKER -o breaker.svg
  python -m dxf2svg list    drawing.dxf
"""

import argparse
import sys
import os
import logging

from .converter import DXFConverter
from .core.svg_builder import BuildConfig


def main():
    parser = argparse.ArgumentParser(
        prog="dxf2svg",
        description="DXF → SVG converter preserving nested blocks, geometry, and layer data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )
    parser.add_argument("command", choices=["convert", "audit", "symbols", "block", "list"])
    parser.add_argument("dxf_file", help="Input DXF file path")
    parser.add_argument("block_name", nargs="?", help="Block name (for 'block' command)")
    parser.add_argument("-o", "--output", help="Output file or directory")
    parser.add_argument("--no-flip-y", action="store_true", help="Disable Y-axis flip")
    parser.add_argument("--stroke-scale", type=float, default=1.0, help="Stroke width multiplier")
    parser.add_argument("--width", type=float, help="Target SVG width (px)")
    parser.add_argument("--height", type=float, help="Target SVG height (px)")
    parser.add_argument("--background", help="Background fill color (e.g. #ffffff)")
    parser.add_argument("--no-css", action="store_true", help="Omit embedded CSS")
    parser.add_argument("--show-frozen", action="store_true", help="Keep frozen layers as-is (default: unfold all)")
    parser.add_argument("-v", "--verbose", action="store_true")

    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="[%(levelname)s] %(message)s")

    if not os.path.exists(args.dxf_file):
        print(f"ERROR: File not found: {args.dxf_file}", file=sys.stderr)
        sys.exit(1)

    conv = DXFConverter(
        args.dxf_file,
        unfold_all_layers=not args.show_frozen,
        log_level=log_level,
    )

    cfg = BuildConfig(
        flip_y=not args.no_flip_y,
        stroke_scale=args.stroke_scale,
        target_width=args.width,
        target_height=args.height,
        background=args.background,
        embed_css=not args.no_css,
    )

    # ── commands ──────────────────────────────────────────────────────────────

    if args.command == "list":
        blocks = conv.list_blocks()
        print(f"\nFound {len(blocks)} blocks in {args.dxf_file}:\n")
        for b in sorted(blocks):
            print(f"  • {b}")
        print()

    elif args.command == "audit":
        print(conv.audit())

    elif args.command == "convert":
        out = args.output or _replace_ext(args.dxf_file, ".svg")
        conv.full_drawing(out, config=cfg)
        print(f"\n✓ Saved: {out}")

    elif args.command == "block":
        if not args.block_name:
            print("ERROR: 'block' command requires a block name argument", file=sys.stderr)
            sys.exit(1)
        out = args.output or f"{_safe_id(args.block_name)}.svg"
        conv.block_to_svg(args.block_name, out, config=cfg)
        print(f"\n✓ Saved: {out}")

    elif args.command == "symbols":
        out_dir = args.output or "symbols"
        conv.symbol_library(out_dir, config=cfg)
        print(f"\n✓ Symbol library saved to: {out_dir}/")


def _replace_ext(path: str, ext: str) -> str:
    return os.path.splitext(path)[0] + ext


def _safe_id(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


if __name__ == "__main__":
    main()
