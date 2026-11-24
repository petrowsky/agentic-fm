#!/usr/bin/env python3
"""
fm_icon_extract.py -- Extract SVG icons from FileMaker layout object XML.

Parses fmxmlsnippet LayoutObjectList XML (Button, ButtonBar, etc.) and extracts
all embedded SVG icons from hex-encoded <Stream> elements.  Each icon is decoded
and optionally saved as an individual .svg file.

No external dependencies -- uses the Python standard library only.

Usage:
    # Extract icons and print JSON report to stdout
    python3 agent/scripts/fm_icon_extract.py agent/sandbox/nav_bar.xml

    # Extract icons and save individual SVG files to a directory
    python3 agent/scripts/fm_icon_extract.py agent/sandbox/nav_bar.xml --output-dir agent/sandbox/icons/

    # Output only the JSON report (no individual files)
    python3 agent/scripts/fm_icon_extract.py agent/sandbox/nav_bar.xml --json
"""

from __future__ import annotations


import argparse
import binascii
import json
import os
import re
import sys
import xml.etree.ElementTree as ET

# ---------------------------------------------------------------------------
# SVG analysis helpers
# ---------------------------------------------------------------------------

def is_stroke_based(svg_text: str) -> bool:
    """Detect whether an SVG uses strokes rather than fills.

    Heuristic: if the root <svg> or any child element has a stroke attribute
    set to something other than "none" AND fill is "none", it is stroke-based.
    Also detects the common Lucide/Feather/Tabler pattern of
    stroke="currentColor" fill="none" on the root element.
    """
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return False

    def _check(el):
        stroke = el.get("stroke", "").lower()
        fill = el.get("fill", "").lower()
        # Classic stroke-based icon pattern
        if stroke and stroke != "none" and fill == "none":
            return True
        # Check style attribute for inline stroke
        style = el.get("style", "")
        return "stroke" in style and "fill:none" in style.replace(" ", "")

    # Check root element
    if _check(root):
        return True

    # Check all descendants
    for el in root.iter():
        if _check(el):
            return True

    return False


def has_fm_fill_class(svg_text: str) -> bool:
    """Check if the SVG uses FileMaker's fm_fill class."""
    return "fm_fill" in svg_text


def extract_viewbox(svg_text: str) -> str | None:
    """Extract the viewBox attribute from the SVG root."""
    try:
        root = ET.fromstring(svg_text)
        return root.get("viewBox")
    except ET.ParseError:
        return None


def svg_dimensions(svg_text: str) -> tuple[str | None, str | None]:
    """Extract width and height from the SVG root."""
    try:
        root = ET.fromstring(svg_text)
        return root.get("width"), root.get("height")
    except ET.ParseError:
        return None, None


# ---------------------------------------------------------------------------
# Hex encoding / decoding
# ---------------------------------------------------------------------------

def hex_decode(hex_str: str) -> str:
    """Decode a hex string to UTF-8 text."""
    clean = re.sub(r"\s+", "", hex_str)
    return binascii.unhexlify(clean).decode("utf-8")


def hex_encode(text: str) -> str:
    """Encode UTF-8 text to uppercase hex string."""
    return text.encode("utf-8").hex().upper()


# ---------------------------------------------------------------------------
# XML parsing and icon extraction
# ---------------------------------------------------------------------------

def _find_label(obj_element: ET.Element) -> str | None:
    """Extract the label text from a Button/Object's LabelCalc."""
    label_calc = obj_element.find(".//LabelCalc/Calculation")
    if label_calc is not None and label_calc.text:
        # Strip CDATA wrapper quotes: "Dashboard" -> Dashboard
        text = label_calc.text.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        return text
    return None


def _find_parent_context(obj_element: ET.Element, root: ET.Element) -> dict:
    """Determine the parent container type (ButtonBar, Button, etc.)."""
    # Walk up isn't easy with ElementTree; we annotate during traversal instead.
    return {}


def extract_icons(xml_path: str | None = None, xml_text: str | None = None) -> list[dict]:
    """Extract all SVG icons from an FM layout object XML file.

    Args:
        xml_path: Path to the XML file (mutually exclusive with xml_text).
        xml_text: Raw XML string (mutually exclusive with xml_path).

    Returns:
        List of dicts, each containing:
            - index: int (0-based position)
            - button_name: str or None
            - label: str or None
            - svg_text: str (decoded SVG)
            - hex_data: str (original hex)
            - is_stroke: bool
            - has_fm_fill: bool
            - viewbox: str or None
            - stream_size: int (from the size attribute)
            - byte_size: int (actual decoded byte count)
            - glph_hex: str or None (the GLPH stream hex if present)
    """
    if xml_path:
        tree = ET.parse(xml_path)
        root = tree.getroot()
    elif xml_text:
        root = ET.fromstring(xml_text)
    else:
        raise ValueError("Provide either xml_path or xml_text")

    icons = []
    idx = 0

    # Find all Object elements that contain SVG streams.
    # Walk the entire tree and look for <Stream> with <Type>SVG </Type>.
    # Then walk back up to find the enclosing Object for context.
    #
    # ElementTree doesn't support parent references, so we build a parent map.
    parent_map = {child: parent for parent in root.iter() for child in parent}

    for stream_el in root.iter("Stream"):
        type_el = stream_el.find("Type")
        if type_el is None or type_el.text is None:
            continue
        if type_el.text.strip() != "SVG":
            continue

        hex_el = stream_el.find("HexData")
        if hex_el is None or not hex_el.text:
            continue

        hex_data = hex_el.text.strip()
        try:
            svg_text = hex_decode(hex_data)
        except (ValueError, UnicodeDecodeError) as exc:
            print(f"WARNING: Could not decode SVG hex at index {idx}: {exc}",
                  file=sys.stderr)
            idx += 1
            continue

        stream_size = int(stream_el.get("size", "0"))
        byte_size = len(svg_text.encode("utf-8"))

        # Find the GLPH stream sibling
        glph_hex = None
        button_obj = parent_map.get(stream_el)
        if button_obj is not None:
            for sibling in button_obj:
                if sibling.tag == "Stream":
                    sib_type = sibling.find("Type")
                    if sib_type is not None and sib_type.text and sib_type.text.strip() == "GLPH":
                        sib_hex = sibling.find("HexData")
                        if sib_hex is not None and sib_hex.text:
                            glph_hex = sib_hex.text.strip()

        # Walk up to find the enclosing Object element for name/label
        button_name = None
        label = None
        el = stream_el
        while el is not None:
            el = parent_map.get(el)
            if el is not None and el.tag == "Object":
                button_name = el.get("name")
                label = _find_label(el)
                break

        icons.append({
            "index": idx,
            "button_name": button_name,
            "label": label,
            "svg_text": svg_text,
            "hex_data": hex_data,
            "is_stroke": is_stroke_based(svg_text),
            "has_fm_fill": has_fm_fill_class(svg_text),
            "viewbox": extract_viewbox(svg_text),
            "stream_size": stream_size,
            "byte_size": byte_size,
            "glph_hex": glph_hex,
        })
        idx += 1

    return icons


def save_icons(icons: list[dict], output_dir: str, prefix: str = "icon") -> list[str]:
    """Save extracted icons as individual SVG files.

    File naming uses the button label if available, otherwise index-based.
    Returns list of file paths written.
    """
    os.makedirs(output_dir, exist_ok=True)
    paths = []

    for icon in icons:
        # Build a clean filename from the label or button name
        name = icon.get("label") or icon.get("button_name") or ""
        if name:
            # Sanitise: lowercase, replace non-alnum with underscore
            clean = re.sub(r"[^a-zA-Z0-9]+", "_", name).strip("_").lower()
        else:
            clean = f"{prefix}_{icon['index']}"

        filename = f"{clean}.svg"
        filepath = os.path.join(output_dir, filename)

        # Avoid overwrites with duplicate names
        counter = 1
        while os.path.exists(filepath):
            filepath = os.path.join(output_dir, f"{clean}_{counter}.svg")
            counter += 1

        with open(filepath, "w", encoding="utf-8") as f:
            f.write(icon["svg_text"])
        paths.append(filepath)

    return paths


def icons_to_report(icons: list[dict]) -> list[dict]:
    """Produce a JSON-friendly report (strips bulky svg_text and hex_data)."""
    report = []
    for icon in icons:
        entry = {
            "index": icon["index"],
            "button_name": icon["button_name"],
            "label": icon["label"],
            "is_stroke": icon["is_stroke"],
            "has_fm_fill": icon["has_fm_fill"],
            "viewbox": icon["viewbox"],
            "stream_size": icon["stream_size"],
            "byte_size": icon["byte_size"],
        }
        report.append(entry)
    return report


# ---------------------------------------------------------------------------
# FM SVG formatting
# ---------------------------------------------------------------------------

def prepare_svg_for_fm(svg_text: str) -> str:
    """Prepare an SVG for use as a FileMaker button icon.

    Ensures:
    - viewBox, width, height attributes present
    - Content wrapped in <g class="fm_fill">
    - No stroke attributes (removes them)
    - Proper XML declaration
    """
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return svg_text

    xlink_ns = "http://www.w3.org/1999/xlink"

    # Extract or default viewBox
    viewbox = root.get("viewBox", "0 0 24 24")
    parts = viewbox.split()
    if len(parts) == 4:
        vb_w, vb_h = parts[2], parts[3]
    else:
        vb_w, vb_h = "24", "24"

    # Build a clean SVG root
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        (f'<svg version="1.2" xmlns="http://www.w3.org/2000/svg"'
         f' xmlns:xlink="{xlink_ns}"'),
        (f'\t x="0px" y="0px" width="{vb_w}px" height="{vb_h}px"'
         f' viewBox="{viewbox}">'),
        '<g class="fm_fill">',
    ]

    # Collect all shape elements from the source SVG
    def _collect_shapes(el):
        """Recursively collect shape elements."""
        shapes = []
        tag = el.tag.split("}")[-1] if "}" in el.tag else el.tag
        if tag in ("path", "rect", "circle", "ellipse", "polygon", "polyline", "line"):
            # Clone attributes, strip stroke/fill (fm_fill handles color)
            attrs = dict(el.attrib)
            for attr in ("stroke", "stroke-width", "stroke-linecap", "stroke-linejoin",
                         "stroke-dasharray", "stroke-dashoffset", "stroke-miterlimit",
                         "stroke-opacity", "fill", "fill-opacity", "fill-rule",
                         "opacity", "class", "style"):
                attrs.pop(attr, None)
            # Remove namespace prefixes from attribute names
            clean_attrs = {}
            for k, v in attrs.items():
                k_clean = k.split("}")[-1] if "}" in k else k
                clean_attrs[k_clean] = v
            shapes.append((tag, clean_attrs))
        for child in el:
            shapes.extend(_collect_shapes(child))
        return shapes

    shapes = _collect_shapes(root)

    for tag, attrs in shapes:
        attr_str = " ".join(f'{k}="{v}"' for k, v in attrs.items())
        lines.append(f"<{tag} {attr_str}/>")

    lines.append("</g>")
    lines.append("</svg>")
    lines.append("")  # trailing newline

    return "\r\n".join(lines)


# An SVG icon stream as FileMaker writes it; the groups split out the size
# attribute and the hex payload so a replacement can swap just those two.
_SVG_STREAM_RE = re.compile(
    r'(<Stream\s+size=")(\d+)(">\s*'
    r'<Type>SVG </Type>\s*'
    r'<HexData>)([0-9A-Fa-f\s]+)(</HexData>\s*'
    r'</Stream>)',
    re.DOTALL,
)


def replace_icons_in_xml(xml_text: str, replacements: dict[int, str]) -> str:
    """Replace SVG icons in layout XML text, leaving every other byte as it was.

    Args:
        xml_text: The layout object XML (fmxmlsnippet LayoutObjectList).
        replacements: Dict mapping icon index (the order extract_icons reports)
            to new SVG text.

    Updates each stream's HexData and its size attribute; the GLPH stream is
    left untouched. The replacement works on the text rather than re-serializing
    a parsed tree: a serializer writes a carriage return in element text raw,
    and FileMaker reads a raw CR as a line feed, so a multi-line text object in
    the same snippet would paste back with its returns changed. Indexes out of
    range raise IndexError.
    """
    matches = list(_SVG_STREAM_RE.finditer(xml_text))
    for i in replacements:
        if i < 0 or i >= len(matches):
            raise IndexError(f"Icon index {i} out of range (0-{len(matches) - 1})")

    # Apply replacements in reverse order to preserve offsets
    result = xml_text
    for i in sorted(replacements, reverse=True):
        m = matches[i]
        new_svg = replacements[i]
        new_hex = hex_encode(new_svg)
        new_size = len(new_svg.encode("utf-8"))
        replacement = f"{m.group(1)}{new_size}{m.group(3)}{new_hex}{m.group(5)}"
        result = result[:m.start()] + replacement + result[m.end():]
    return result


def replace_icon_in_xml(xml_text: str, icon_index: int, new_svg_text: str,
                        icons: list[dict] | None = None) -> str:
    """Replace the SVG at the given index in the layout XML with new SVG content.

    Updates both the HexData and the Stream size attribute.
    The GLPH stream is left untouched.
    """
    return replace_icons_in_xml(xml_text, {icon_index: new_svg_text})


def replace_icons_in_file(xml_path: str, replacements: dict[int, str],
                          output_path: str | None = None) -> str:
    """Replace multiple icons in a layout XML file.

    Args:
        xml_path: Path to the source XML file.
        replacements: Dict mapping icon index → new SVG text.
        output_path: Where to write the result. If None, returns the XML string.

    Returns:
        The modified XML as a string.
    """
    # newline="" keeps a CR in the file as it is instead of translating it.
    with open(xml_path, "r", encoding="utf-8", newline="") as f:
        xml_text = f.read()

    count = len(_SVG_STREAM_RE.findall(xml_text))
    in_range = {}
    for i, svg in replacements.items():
        if 0 <= i < count:
            in_range[i] = svg
        else:
            print(f"WARNING: Skipping out-of-range index {i}", file=sys.stderr)
    result = replace_icons_in_xml(xml_text, in_range)

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w", encoding="utf-8", newline="") as f:
            f.write(result)
        print(f"Wrote updated XML to {output_path}", file=sys.stderr)

    return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Extract SVG icons from FileMaker layout object XML."
    )
    parser.add_argument("input", help="Path to the FM layout XML file")
    parser.add_argument("--output-dir", "-o",
                        help="Directory to save individual SVG files")
    parser.add_argument("--json", action="store_true", dest="json_report",
                        help="Print JSON report to stdout")
    parser.add_argument("--full", action="store_true",
                        help="Include svg_text in JSON output")
    args = parser.parse_args()

    if not os.path.isfile(args.input):
        print(f"ERROR: File not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    icons = extract_icons(xml_path=args.input)

    if not icons:
        print("No SVG icons found in the input file.", file=sys.stderr)
        sys.exit(0)

    print(f"Found {len(icons)} SVG icon(s).", file=sys.stderr)

    if args.output_dir:
        paths = save_icons(icons, args.output_dir)
        for p in paths:
            print(f"  Saved: {p}", file=sys.stderr)

    if args.json_report or not args.output_dir:
        if args.full:
            report = icons
        else:
            report = icons_to_report(icons)
        print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
