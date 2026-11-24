#!/usr/bin/env python3
"""
fm_svg_convert.py -- Convert stroke-based SVGs to FileMaker-compatible filled SVGs.

FileMaker button icons only support filled shapes (no strokes). Modern icon
libraries (Lucide, Heroicons, Tabler, etc.) use stroke-based SVGs that must be
converted before use.

Pipeline:
    1. Rasterise the SVG to a high-resolution PNG (cairosvg)
    2. Threshold to a 1-bit bitmap (Pillow)
    3. Trace the bitmap back to filled vector paths (potrace CLI)
    4. Scale paths to the original viewBox and wrap in FM format

Dependencies (install in a venv):
    pip install cairosvg Pillow

System dependency:
    brew install potrace    # macOS
    apt-get install potrace # Debian/Ubuntu

Usage:
    # Convert a single SVG
    python3 agent/scripts/fm_svg_convert.py input.svg -o output.svg

    # Convert and format for FileMaker (adds fm_fill class wrapper)
    python3 agent/scripts/fm_svg_convert.py input.svg -o output.svg --fm

    # Check if dependencies are installed
    python3 agent/scripts/fm_svg_convert.py --check-deps
"""

from __future__ import annotations


import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# Dependency checking
# ---------------------------------------------------------------------------

def check_dependencies() -> dict:
    """Check which dependencies are available.

    Returns dict with keys: cairosvg, pillow, potrace — values are bool.
    """
    result = {}

    try:
        import cairosvg  # noqa: F401
        result["cairosvg"] = True
    except ImportError:
        result["cairosvg"] = False

    try:
        from PIL import Image  # noqa: F401
        result["pillow"] = True
    except ImportError:
        result["pillow"] = False

    result["potrace"] = shutil.which("potrace") is not None

    return result


def assert_dependencies():
    """Raise an error if required dependencies are missing."""
    deps = check_dependencies()
    missing = [name for name, ok in deps.items() if not ok]
    if missing:
        msg_lines = ["Missing dependencies for stroke-to-fill conversion:", ""]
        if "cairosvg" in missing or "pillow" in missing:
            pip_pkgs = []
            if "cairosvg" in missing:
                pip_pkgs.append("cairosvg")
            if "pillow" in missing:
                pip_pkgs.append("Pillow")
            msg_lines.append(f"  pip install {' '.join(pip_pkgs)}")
        if "potrace" in missing:
            msg_lines.append("  brew install potrace   # macOS")
            msg_lines.append("  apt-get install potrace  # Debian/Ubuntu")
        msg_lines.append("")
        msg_lines.append("See agent/docs/VENV_SETUP.md or the icon-swap skill for setup instructions.")
        raise RuntimeError("\n".join(msg_lines))


# ---------------------------------------------------------------------------
# SVG analysis
# ---------------------------------------------------------------------------

def is_stroke_based(svg_text: str) -> bool:
    """Detect whether an SVG uses strokes rather than fills."""
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return False

    def _check(el):
        stroke = el.get("stroke", "").lower()
        fill = el.get("fill", "").lower()
        if stroke and stroke != "none" and fill == "none":
            return True
        style = el.get("style", "")
        if "stroke" in style and "fill:none" in style.replace(" ", ""):
            return True
        return False

    if _check(root):
        return True
    for el in root.iter():
        if _check(el):
            return True
    return False


def get_viewbox(svg_text: str) -> tuple[float, float, float, float]:
    """Parse the viewBox from SVG text. Returns (min_x, min_y, width, height)."""
    root = ET.fromstring(svg_text)
    vb = root.get("viewBox", "0 0 24 24")
    parts = [float(x) for x in vb.split()]
    if len(parts) == 4:
        return tuple(parts)
    return (0.0, 0.0, 24.0, 24.0)


# ---------------------------------------------------------------------------
# Stroke-to-fill conversion
# ---------------------------------------------------------------------------

def stroke_to_fill(svg_text: str, render_size: int = 1024,
                   potrace_params: dict | None = None) -> str:
    """Convert a stroke-based SVG to a filled SVG via rasterise-then-trace.

    Matches the elemental-svg.com pipeline:
    1. Compute scale so the larger dimension renders at ~render_size pixels
    2. Force all colours to black, render SVG to high-res PNG
    3. Threshold to 1-bit bitmap, flip vertically (BMP is bottom-up)
    4. Trace with potrace CLI
    5. Scale all path coordinates back by 1/scale (simple regex on all numbers)
    6. Wrap in FM-compatible SVG structure

    Args:
        svg_text: The stroke-based SVG markup.
        render_size: Target resolution for the larger dimension (default 1024).
        potrace_params: Optional dict of potrace parameters.

    Returns:
        SVG markup with filled paths, viewBox matching the original.
    """
    assert_dependencies()

    import cairosvg
    from PIL import Image

    params = {"turdsize": 2, "alphamax": 1.0, "opttolerance": 0.2}
    if potrace_params:
        params.update(potrace_params)

    orig_vb = get_viewbox(svg_text)
    vb_x, vb_y, vb_w, vb_h = orig_vb

    # Scale factor: match elemental-svg — scale so larger dim ≈ render_size
    scale = max(2, render_size / max(vb_w, vb_h))
    canvas_w = round(vb_w * scale)
    canvas_h = round(vb_h * scale)

    # Force all colours to black for maximum contrast during tracing
    # Match elemental-svg's regex approach: replace non-"none" fills/strokes
    svg_for_render = svg_text
    svg_for_render = re.sub(r'currentColor', '#000000', svg_for_render, flags=re.IGNORECASE)
    svg_for_render = re.sub(r'fill\s*=\s*"(?!none)[^"]*"', 'fill="#000000"', svg_for_render, flags=re.IGNORECASE)
    svg_for_render = re.sub(r'stroke\s*=\s*"(?!none)[^"]*"', 'stroke="#000000"', svg_for_render, flags=re.IGNORECASE)
    svg_for_render = re.sub(r'opacity="[^"]*"', 'opacity="1"', svg_for_render)

    with tempfile.TemporaryDirectory() as tmpdir:
        png_path = os.path.join(tmpdir, "render.png")
        bmp_path = os.path.join(tmpdir, "render.bmp")
        svg_out_path = os.path.join(tmpdir, "traced.svg")

        # Step 1: Rasterise SVG to PNG at computed canvas size
        cairosvg.svg2png(
            bytestring=svg_for_render.encode("utf-8"),
            write_to=png_path,
            output_width=canvas_w,
            output_height=canvas_h,
        )

        # Step 2: Composite on white, threshold to 1-bit, flip for potrace
        img = Image.open(png_path).convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.paste(img, mask=img)
        grey = bg.convert("L")

        # Threshold: darker than 128 → black
        bw = grey.point(lambda x: 0 if x < 128 else 255, mode="1")

        # Do NOT flip the BMP. Potrace handles the BMP bottom-up coordinate
        # system internally via its translate(0,H) scale(S,-S) transform.
        # Pre-flipping would double-flip, producing upside-down output.
        bw.save(bmp_path)

        # Step 3: Trace with potrace
        cmd = [
            "potrace",
            "-s",  # SVG output
            "-t", str(params["turdsize"]),
            "-a", str(params["alphamax"]),
            "-O", str(params["opttolerance"]),
            "-o", svg_out_path,
            bmp_path,
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"potrace failed: {proc.stderr}")

        # Step 4: Read traced SVG, extract paths, scale back
        with open(svg_out_path, "r", encoding="utf-8") as f:
            traced_svg = f.read()

    return _build_fm_svg_from_traced(traced_svg, orig_vb, scale)


def _transform_potrace_path(path_d: str, combined_scale: float,
                            vb_h: float) -> str:
    """Transform potrace path data into viewBox coordinates.

    Potrace CLI outputs paths with a transform="translate(0,H) scale(S,-S)".
    The raw path uses:
    - M x y      (absolute) → x_vb = x * cs,  y_vb = vb_h - y * cs
    - m dx dy    (relative) → dx_vb = dx * cs, dy_vb = -(dy * cs)
    - c/l dx dy  (relative) → dx_vb = dx * cs, dy_vb = -(dy * cs)
    - z/Z        (no coords)

    where cs = combined_scale = potrace_inner_scale / render_scale.
    """
    # Tokenize into commands and numbers
    tokens = re.findall(
        r'[MmCcLlZz]|[-+]?\d+\.?\d*',
        path_d,
    )

    result = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok in ('z', 'Z'):
            result.append('z')
            i += 1
        elif tok == 'M':
            # Absolute moveto: consume pairs of (x, y)
            i += 1
            first = True
            while i + 1 < len(tokens) and tokens[i] not in 'MmCcLlZz':
                x = float(tokens[i]) * combined_scale
                y = vb_h - float(tokens[i + 1]) * combined_scale
                if first:
                    result.append(f'M{x:.2f} {y:.2f}')
                    first = False
                else:
                    # Implicit L after M
                    result.append(f'L{x:.2f} {y:.2f}')
                i += 2
        elif tok == 'm':
            # Relative moveto: negate Y deltas
            i += 1
            first = True
            while i + 1 < len(tokens) and tokens[i] not in 'MmCcLlZz':
                dx = float(tokens[i]) * combined_scale
                dy = -(float(tokens[i + 1]) * combined_scale)
                cmd = 'm' if first else 'l'
                result.append(f'{cmd}{dx:.2f} {dy:.2f}')
                first = False
                i += 2
        elif tok == 'c':
            # Relative cubic bezier: groups of 6 numbers (dx1,dy1,dx2,dy2,dx3,dy3)
            i += 1
            parts = []
            while i + 5 < len(tokens) and tokens[i] not in 'MmCcLlZz':
                coords = []
                for j in range(3):
                    dx = float(tokens[i]) * combined_scale
                    dy = -(float(tokens[i + 1]) * combined_scale)
                    coords.append(f'{dx:.2f} {dy:.2f}')
                    i += 2
                parts.append(' '.join(coords))
            if parts:
                result.append('c' + ' '.join(parts))
        elif tok == 'l':
            # Relative lineto: negate Y deltas
            i += 1
            parts = []
            while i + 1 < len(tokens) and tokens[i] not in 'MmCcLlZz':
                dx = float(tokens[i]) * combined_scale
                dy = -(float(tokens[i + 1]) * combined_scale)
                parts.append(f'{dx:.2f} {dy:.2f}')
                i += 2
            if parts:
                result.append('l' + ' '.join(parts))
        elif tok == 'C':
            # Absolute cubic bezier
            i += 1
            parts = []
            while i + 5 < len(tokens) and tokens[i] not in 'MmCcLlZz':
                coords = []
                for j in range(3):
                    x = float(tokens[i]) * combined_scale
                    y = vb_h - float(tokens[i + 1]) * combined_scale
                    coords.append(f'{x:.2f} {y:.2f}')
                    i += 2
                parts.append(' '.join(coords))
            if parts:
                result.append('C' + ' '.join(parts))
        elif tok == 'L':
            # Absolute lineto
            i += 1
            parts = []
            while i + 1 < len(tokens) and tokens[i] not in 'MmCcLlZz':
                x = float(tokens[i]) * combined_scale
                y = vb_h - float(tokens[i + 1]) * combined_scale
                parts.append(f'{x:.2f} {y:.2f}')
                i += 2
            if parts:
                result.append('L' + ' '.join(parts))
        else:
            # Unknown token, skip
            i += 1

    return ' '.join(result)


def _build_fm_svg_from_traced(traced_svg: str, orig_vb: tuple,
                              render_scale: float) -> str:
    """Extract paths from potrace SVG output and build FM-compatible SVG.

    Potrace CLI outputs paths in a coordinate system with a Y-axis flip.
    The <g> has transform="translate(0,H) scale(S,-S)" where S is typically
    0.1. This function applies that transform to each path's coordinates,
    mapping them into the original viewBox space.
    """
    try:
        root = ET.fromstring(traced_svg)
    except ET.ParseError:
        return traced_svg

    vb_x, vb_y, vb_w, vb_h = orig_vb

    # Parse potrace's <g transform="translate(0,H) scale(Sx,Sy)">
    potrace_scale = 0.1  # default
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "g":
            transform = el.get("transform", "")
            scale_match = re.search(r'scale\(([^,)]+)', transform)
            if scale_match:
                potrace_scale = abs(float(scale_match.group(1)))
            break

    # Combined scale: potrace raw coords * potrace_scale = pixel coords,
    # pixel coords / render_scale = viewBox coords.
    combined_scale = potrace_scale / render_scale

    # Extract and transform all path d attributes
    path_data = []
    for el in root.iter():
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag == "path":
            d = el.get("d", "")
            if d:
                path_data.append(
                    _transform_potrace_path(d, combined_scale, vb_h)
                )

    if not path_data:
        return traced_svg

    # Build FM SVG matching elemental-svg output format
    viewbox = f"{vb_x} {vb_y} {vb_w} {vb_h}"
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{vb_w}" height="{vb_h}"'
        f' viewBox="{viewbox}"'
        f' class="fm_fill" fill="currentColor">',
    ]
    for d in path_data:
        lines.append(f'    <path d="{d}" fill="inherit" stroke="none"/>')
    lines.append("</svg>")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# FM SVG formatting
# ---------------------------------------------------------------------------

def format_for_fm(svg_text: str) -> str:
    """Format an SVG for FileMaker button icons.

    Matches elemental-svg processSVGForFileMaker():
    - class="fm_fill" and fill="currentColor" on the root <svg> element
    - Each shape gets fill="inherit" stroke="none"
    - viewBox, width, height ensured
    - Removes opacity, fill-opacity, stroke-opacity
    - Strips style attributes, migrates to XML presentation attrs

    If the SVG already has fm_fill class, returns it as-is.
    """
    if "fm_fill" in svg_text:
        return svg_text

    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return svg_text

    viewbox = root.get("viewBox", "0 0 24 24")
    parts = viewbox.split()
    vb_w = parts[2] if len(parts) >= 3 else "24"
    vb_h = parts[3] if len(parts) >= 4 else "24"

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg"'
        f' width="{vb_w}" height="{vb_h}"'
        f' viewBox="{viewbox}"'
        f' class="fm_fill" fill="currentColor">',
    ]

    shape_tags = {"path", "rect", "circle", "ellipse", "polygon", "polyline", "line"}

    def _collect(el):
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag in shape_tags:
            attrs = dict(el.attrib)
            # Remove styling attributes — fm_fill + inherit handles colour
            for attr in ("stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
                         "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit",
                         "stroke-opacity", "fill", "fill-opacity", "opacity",
                         "class", "style", "fill-rule"):
                attrs.pop(attr, None)
            # Clean namespace prefixes
            clean = {}
            for k, v in attrs.items():
                k = k.split("}")[-1] if "}" in k else k
                clean[k] = v
            attr_str = " ".join(f'{k}="{v}"' for k, v in clean.items())
            lines.append(f'    <{tag} {attr_str} fill="inherit" stroke="none"/>')
        for child in el:
            _collect(child)

    _collect(root)

    lines.append("</svg>")
    lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Icon library fetching
# ---------------------------------------------------------------------------

# CDN URL patterns for supported icon libraries.
# {name} is replaced with the kebab-case icon name.
ICON_LIBRARIES = {
    "lucide": {
        "label": "Lucide",
        "url": "https://unpkg.com/lucide-static@latest/icons/{name}.svg",
        "style": "stroke",
        "website": "https://lucide.dev",
    },
    "heroicons-outline": {
        "label": "Heroicons (outline)",
        "url": "https://unpkg.com/heroicons@2.2.0/24/outline/{name}.svg",
        "style": "stroke",
        "website": "https://heroicons.com",
    },
    "heroicons-solid": {
        "label": "Heroicons (solid)",
        "url": "https://unpkg.com/heroicons@2.2.0/24/solid/{name}.svg",
        "style": "fill",
        "website": "https://heroicons.com",
    },
    "tabler": {
        "label": "Tabler Icons",
        "url": "https://unpkg.com/@tabler/icons@3.31.0/icons/outline/{name}.svg",
        "style": "stroke",
        "website": "https://tabler.io/icons",
    },
    "tabler-filled": {
        "label": "Tabler Icons (filled)",
        "url": "https://unpkg.com/@tabler/icons@3.31.0/icons/filled/{name}.svg",
        "style": "fill",
        "website": "https://tabler.io/icons",
    },
    "phosphor": {
        "label": "Phosphor Icons",
        "url": "https://unpkg.com/@phosphor-icons/core@2.1.1/assets/regular/{name}.svg",
        "style": "fill",
        "website": "https://phosphoricons.com",
    },
    "bootstrap": {
        "label": "Bootstrap Icons",
        "url": "https://unpkg.com/bootstrap-icons@1.11.3/icons/{name}.svg",
        "style": "fill",
        "website": "https://icons.getbootstrap.com",
    },
    "iconoir": {
        "label": "Iconoir",
        "url": "https://unpkg.com/iconoir@7.9.0/icons/regular/{name}.svg",
        "style": "stroke",
        "website": "https://iconoir.com",
    },
    "mdi": {
        "label": "Material Design Icons",
        "url": "https://unpkg.com/@mdi/svg@7.4.47/svg/{name}.svg",
        "style": "fill",
        "website": "https://materialdesignicons.com",
    },
    "ionicons-outline": {
        "label": "Ionicons (outline)",
        "url": "https://unpkg.com/ionicons@7.4.0/dist/svg/{name}-outline.svg",
        "style": "stroke",
        "website": "https://ionic.io/ionicons",
    },
    "ionicons-filled": {
        "label": "Ionicons (filled)",
        "url": "https://unpkg.com/ionicons@7.4.0/dist/svg/{name}-sharp.svg",
        "style": "fill",
        "website": "https://ionic.io/ionicons",
    },
}


def fetch_icon(library: str, icon_name: str) -> str | None:
    """Fetch an SVG icon from a CDN.

    Args:
        library: Key from ICON_LIBRARIES (e.g. "lucide", "heroicons-outline").
        icon_name: The kebab-case icon name (e.g. "layout-dashboard").

    Returns:
        SVG text, or None if the icon was not found.
    """
    import urllib.request
    import urllib.error

    lib = ICON_LIBRARIES.get(library)
    if not lib:
        raise ValueError(f"Unknown library: {library}. "
                         f"Available: {', '.join(ICON_LIBRARIES.keys())}")

    url = lib["url"].format(name=icon_name)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "agentic-fm/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    except urllib.error.URLError:
        return None
    return None


def list_libraries() -> list[dict]:
    """List available icon libraries with metadata."""
    return [
        {"id": k, "label": v["label"], "style": v["style"], "website": v["website"]}
        for k, v in ICON_LIBRARIES.items()
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Convert stroke-based SVGs to FileMaker-compatible filled SVGs."
    )
    parser.add_argument("input", nargs="?", help="Input SVG file path")
    parser.add_argument("-o", "--output", help="Output SVG file path")
    parser.add_argument("--fm", action="store_true",
                        help="Format output for FileMaker (fm_fill class)")
    parser.add_argument("--check-deps", action="store_true",
                        help="Check if required dependencies are installed")
    parser.add_argument("--list-libraries", action="store_true",
                        help="List available icon libraries")
    parser.add_argument("--fetch", metavar="LIBRARY:ICON",
                        help="Fetch an icon from a library (e.g. lucide:home)")
    parser.add_argument("--render-size", type=int, default=1024,
                        help="Rasterisation resolution (default: 1024)")
    args = parser.parse_args()

    if args.check_deps:
        deps = check_dependencies()
        import json
        print(json.dumps(deps, indent=2))
        all_ok = all(deps.values())
        if not all_ok:
            print("\nMissing dependencies:", file=sys.stderr)
            if not deps["cairosvg"]:
                print("  pip install cairosvg", file=sys.stderr)
            if not deps["pillow"]:
                print("  pip install Pillow", file=sys.stderr)
            if not deps["potrace"]:
                print("  brew install potrace  # macOS", file=sys.stderr)
        else:
            print("\nAll dependencies available.", file=sys.stderr)
        sys.exit(0 if all_ok else 1)

    if args.list_libraries:
        import json
        print(json.dumps(list_libraries(), indent=2))
        sys.exit(0)

    if args.fetch:
        parts = args.fetch.split(":", 1)
        if len(parts) != 2:
            print("ERROR: --fetch format is LIBRARY:ICON (e.g. lucide:home)",
                  file=sys.stderr)
            sys.exit(1)
        lib, name = parts
        svg = fetch_icon(lib, name)
        if svg is None:
            print(f"ERROR: Icon '{name}' not found in library '{lib}'",
                  file=sys.stderr)
            sys.exit(1)
        if args.fm:
            if is_stroke_based(svg):
                svg = stroke_to_fill(svg, render_size=args.render_size)
            svg = format_for_fm(svg)
        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(svg)
            print(f"Saved to {args.output}", file=sys.stderr)
        else:
            print(svg)
        sys.exit(0)

    if not args.input:
        parser.print_help()
        sys.exit(1)

    if not os.path.isfile(args.input):
        print(f"ERROR: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    with open(args.input, "r", encoding="utf-8") as f:
        svg_text = f.read()

    if is_stroke_based(svg_text):
        print("Detected stroke-based SVG, converting to filled paths...",
              file=sys.stderr)
        svg_text = stroke_to_fill(svg_text, render_size=args.render_size)
    else:
        print("SVG is already fill-based, no conversion needed.", file=sys.stderr)

    if args.fm:
        svg_text = format_for_fm(svg_text)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(svg_text)
        print(f"Saved to {args.output}", file=sys.stderr)
    else:
        print(svg_text)


if __name__ == "__main__":
    main()
