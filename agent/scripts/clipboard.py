#!/usr/bin/env python3
"""
clipboard.py -- Read and write FileMaker fmxmlsnippets via the macOS clipboard.

FileMaker stores clipboard objects as proprietary AppleScript descriptor classes,
not as plain text. This script converts between those classes and fmxmlsnippet XML.

Menu objects (CustomMenu, CustomMenuSet) are an exception: FileMaker stores them
as UTF-16 encoded Unicode text («class ut16») rather than a binary FM descriptor.
This script handles both paths automatically.

IMPORTANT: Do not use pbpaste/pbcopy. They corrupt multi-byte UTF-8 characters
(such as ≠ ≤ ≥ ¶) that are common in FileMaker calculations.

See agent/docs/CLIPBOARD.md for full technical background.

Usage:
    Read FM objects from clipboard, print XML to stdout:
        python agent/scripts/clipboard.py read

    Read FM objects from clipboard, save to file:
        python agent/scripts/clipboard.py read agent/sandbox/output.xml

    Write XML file to clipboard (class auto-detected from XML content):
        python agent/scripts/clipboard.py write agent/sandbox/myscript.xml

    Write XML file to clipboard with an explicit class override:
        python agent/scripts/clipboard.py write agent/sandbox/myscript.xml --class XMSC
"""

import argparse
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

# Optional fast path: PyObjC (pyobjc-framework-Cocoa) lets us read/write the
# clipboard via NSPasteboard directly, bypassing osascript subprocesses and the
# hex-decode round-trip.  Falls back to the osascript path when not installed.
try:
    from AppKit import NSPasteboard, NSData  # type: ignore[import]
    _HAS_APPKIT = True
except ImportError:
    _HAS_APPKIT = False

# FileMaker clipboard class codes
FM_CLASSES = {
    'XMSS': 'Script Steps',
    'XMSC': 'Script',
    'XML2': 'Layout Objects',
    'XMLO': 'Layout Objects (legacy)',
    'XMFD': 'Field Definition',
    'XMFN': 'Custom Function',
    'XMTB': 'Table',
    'XMVL': 'Value List',
    'XMTH': 'Theme',
}

# Map the first XML element found inside an fmxmlsnippet to its clipboard class.
# Elements that map to 'ut16' are stored as UTF-16 Unicode text, not binary descriptors.
XML_ELEMENT_TO_CLASS = {
    'Step':              'XMSS',
    'Script':            'XMSC',
    'CustomFunction':    'XMFN',
    'Field':             'XMFD',
    'BaseTable':         'XMTB',
    'ValueList':         'XMVL',
    'Layout':            'XML2',
    'Theme':             'XMTH',
}

# Class codes that use UTF-16 Unicode text rather than binary FM descriptors
UT16_CLASSES = {'ut16'}

# CorePasteboardFlavorType hex values for each FM class code.
# The 4-byte integer is just the ASCII bytes of the class code interpreted as big-endian.
# e.g. XMSS → 0x58('X') 0x4D('M') 0x53('S') 0x53('S') → 0x584D5353
# The pasteboard type string is: f"CorePasteboardFlavorType 0x{hex_val:08X}"
_FM_CLASS_HEX = {
    'XMSS': 0x584D5353,
    'XMSC': 0x584D5343,
    'XML2': 0x584D4C32,
    'XMLO': 0x584D4C4F,
    'XMFD': 0x584D4644,
    'XMFN': 0x584D464E,
    'XMTB': 0x584D5442,
    'XMVL': 0x584D564C,
    'XMTH': 0x584D5448,
    'ut16': 0x75743136,  # «class ut16» — UTF-16 Unicode text (custom menus)
}


def _pb_type_str(cls):
    """Return the CorePasteboardFlavorType string for a class code."""
    return f"CorePasteboardFlavorType 0x{_FM_CLASS_HEX[cls]:08X}"


def _nspasteboard_detect():
    """Detect which FM class is on the clipboard via NSPasteboard. Returns code or None."""
    pb = NSPasteboard.generalPasteboard()
    for cls in _FM_CLASS_HEX:
        if pb.dataForType_(_pb_type_str(cls)) is not None:
            return cls
    return None


def _nspasteboard_read_bytes(cls):
    """Read raw clipboard bytes for a given FM class via NSPasteboard. Returns bytes or None."""
    pb = NSPasteboard.generalPasteboard()
    data = pb.dataForType_(_pb_type_str(cls))
    if data is None:
        return None
    return data.bytes().tobytes()


def _nspasteboard_write(cls, raw_bytes):
    """Write raw bytes to the clipboard under the given FM class type. Returns True on success."""
    pb = NSPasteboard.generalPasteboard()
    pb.clearContents()
    ns_data = NSData.dataWithBytes_length_(raw_bytes, len(raw_bytes))
    return bool(pb.setData_forType_(ns_data, _pb_type_str(cls)))


def detect_clipboard_class():
    """Return the FM class code currently on the clipboard, or None.

    Returns 'ut16' when the clipboard holds FM menu objects (Unicode text),
    or a four-letter FM binary descriptor code (e.g. 'XMSS') otherwise.
    """
    if _HAS_APPKIT:
        return _nspasteboard_detect()

    class_list = ', '.join(f'\u00abclass {c}\u00bb' for c in FM_CLASSES)
    script = f"""try
    set allowed to {{{class_list}}}
    set clipboardType to item 1 of item 1 of (clipboard info) as class
    if clipboardType is in allowed then
        return clipboardType as string
    end if
    -- Check for menu objects stored as UTF-16 Unicode text
    repeat with typeInfo in (clipboard info)
        set theType to item 1 of typeInfo
        if theType is \u00abclass ut16\u00bb then
            return "ut16"
        end if
    end repeat
    return ""
on error
    return ""
end try"""
    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
    cls = result.stdout.strip()
    if cls == 'ut16':
        return 'ut16'
    # AppleScript returns the class as «class XMSS» — extract just the four-letter code
    match = re.search(r'\u00abclass (\w+)\u00bb', cls)
    if match:
        return match.group(1)
    return cls if cls else None


def detect_class_from_xml(xml_text):
    """Infer the correct FM clipboard class from the XML element content."""
    # Try XML parsing first — more robust than regex and naturally handles the
    # menu-vs-steps priority: the first child of the fmxmlsnippet root is always
    # the correct type tag, so <Step> elements nested inside menu action blocks
    # are never seen at this level.
    try:
        root = ET.fromstring(xml_text)
        if len(root) > 0:
            cls = XML_ELEMENT_TO_CLASS.get(root[0].tag)
            if cls:
                return cls
    except ET.ParseError:
        pass

    # Fallback: regex scan for malformed or partially-written XML.
    # Container elements must be checked before 'Step' — both Script and menu XML
    # files contain <Step> elements inside them, which would otherwise match XMSS first.
    for element in ('CustomMenuSet', 'CustomMenu'):
        if re.search(rf'<{element}[\s>/]', xml_text):
            return 'ut16'
    if re.search(r'<Script[\s>/]', xml_text):
        return 'XMSC'
    for element, cls in XML_ELEMENT_TO_CLASS.items():
        if element in ('CustomMenuSet', 'CustomMenu', 'Script'):
            continue
        if re.search(rf'<{element}[\s>/]', xml_text):
            return cls
    return 'XMSS'  # safe default for steps-only snippets


def read_from_clipboard(output_path=None):
    """Extract FM objects from clipboard and output as formatted XML."""
    cls = detect_clipboard_class()
    if not cls:
        print('ERROR: No FileMaker objects found on clipboard.', file=sys.stderr)
        sys.exit(1)

    if cls in UT16_CLASSES:
        return _read_ut16_from_clipboard(output_path)

    if _HAS_APPKIT:
        raw_bytes = _nspasteboard_read_bytes(cls)
        if raw_bytes is None:
            print(f'ERROR: Could not read {cls} data from clipboard.', file=sys.stderr)
            sys.exit(1)
        xml = raw_bytes.decode('utf-8')
    else:
        # Use "the clipboard as «class XMSS»" rather than "«class XMSS» of (the clipboard)".
        # The "of" form treats the clipboard as a record and fails when the clipboard's
        # primary type is plain text (e.g. a single text label copied in Layout Mode).
        # The "as" coercion form locates the requested type regardless of primary type.
        cls_expr = f'\u00abclass {cls}\u00bb'  # «class XMSS»
        result = subprocess.run(
            ['osascript', '-e', f'the clipboard as {cls_expr}'],
            capture_output=True
        )
        if result.returncode != 0:
            print(f'ERROR: {result.stderr.decode().strip()}', file=sys.stderr)
            sys.exit(1)

        # osascript prints binary descriptors as: «data XMSS<hexstring>»
        # Extract the hex portion, convert to bytes, decode as UTF-8.
        # This avoids the sed/xxd/iconv shell pipeline and its quoting hazards.
        raw = result.stdout.decode('utf-8', errors='replace').strip()
        # Class codes are exactly 4 chars (e.g. XMSS, XML2). Using \w+ was greedy
        # and consumed hex digits that belong to the data, leaving an odd-length capture.
        match = re.search(r'\u00abdata [A-Z0-9]{4}([0-9A-Fa-f]+)\u00bb', raw)
        if not match:
            print(f'ERROR: Unexpected clipboard output:\n{raw[:300]}', file=sys.stderr)
            sys.exit(1)

        hex_str = re.sub(r'\s+', '', match.group(1))
        xml = bytes.fromhex(hex_str).decode('utf-8')

    # Pretty-print with xmllint (included with macOS Xcode command line tools)
    fmt = subprocess.run(['xmllint', '--format', '-'], input=xml.encode('utf-8'), capture_output=True)
    if fmt.returncode == 0:
        xml = fmt.stdout.decode('utf-8')

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml)
        print(f'Saved {cls} ({FM_CLASSES[cls]}) to {output_path}', file=sys.stderr)
    else:
        print(xml)

    return xml


def _read_ut16_from_clipboard(output_path=None):
    """Read a UTF-16 menu object from the clipboard (CustomMenu / CustomMenuSet)."""
    if _HAS_APPKIT:
        raw_bytes = _nspasteboard_read_bytes('ut16')
        if raw_bytes is None:
            print('ERROR: Could not read ut16 data from clipboard.', file=sys.stderr)
            sys.exit(1)
        # NSPasteboard gives us the raw UTF-16 bytes (with BOM); decode directly.
        xml = raw_bytes.decode('utf-16')
    else:
        # Unlike binary FM descriptor classes, osascript returns ut16 clipboard content
        # as plain UTF-8 text (not as «data ut16XXXX»), so we decode stdout directly.
        result = subprocess.run(
            ['osascript', '-e', 'the clipboard as \u00abclass ut16\u00bb'],
            capture_output=True
        )
        if result.returncode != 0:
            print(f'ERROR: {result.stderr.decode().strip()}', file=sys.stderr)
            sys.exit(1)
        xml = result.stdout.decode('utf-8')

    # Pretty-print with xmllint
    fmt = subprocess.run(['xmllint', '--format', '-'], input=xml.encode('utf-8'), capture_output=True)
    if fmt.returncode == 0:
        xml = fmt.stdout.decode('utf-8')

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(xml)
        print(f'Saved ut16 (Menu) to {output_path}', file=sys.stderr)
    else:
        print(xml)

    return xml


def _decode_file(raw_bytes):
    """Decode file bytes, honouring a UTF-16 BOM if present."""
    if raw_bytes[:2] in (b'\xff\xfe', b'\xfe\xff'):
        return raw_bytes.decode('utf-16')
    return raw_bytes.decode('utf-8', errors='replace')


def write_to_clipboard(input_path, cls=None):
    """Write an fmxmlsnippet XML file to the clipboard as FM objects."""
    with open(input_path, 'rb') as f:
        raw_bytes = f.read()

    xml_text = _decode_file(raw_bytes)

    if cls is None:
        cls = detect_class_from_xml(xml_text)

    cls = cls.lower() if cls.lower() in UT16_CLASSES else cls.upper()

    if cls in UT16_CLASSES:
        _write_ut16_to_clipboard(xml_text, input_path)
        return

    if cls not in FM_CLASSES:
        print(f"ERROR: Unknown class '{cls}'. Valid options: {', '.join(FM_CLASSES)}", file=sys.stderr)
        sys.exit(1)

    if _HAS_APPKIT:
        if not _nspasteboard_write(cls, raw_bytes):
            print('ERROR: NSPasteboard write failed.', file=sys.stderr)
            sys.exit(1)
    else:
        hex_data = raw_bytes.hex()
        # «data XMSS<hexdata>» — the AppleScript binary descriptor literal syntax
        script = f'set the clipboard to \u00abdata {cls}{hex_data}\u00bb'
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        if result.returncode != 0:
            print(f'ERROR: {result.stderr.strip()}', file=sys.stderr)
            sys.exit(1)

    print(f'Clipboard ready \u2192 {input_path} as {cls} ({FM_CLASSES[cls]})', file=sys.stderr)


def _write_ut16_to_clipboard(xml_text, input_path):
    """Write a menu XML string to the clipboard as UTF-16 Unicode text («class ut16»)."""
    # Strip any existing XML declaration — FileMaker expects a clean UTF-16 payload.
    # We re-encode as UTF-16 with BOM, which is what FileMaker writes when it copies menus.
    xml_text = re.sub(r'<\?xml[^?]*\?>\s*', '', xml_text, count=1)
    utf16_bytes = xml_text.encode('utf-16')  # includes BOM automatically

    if _HAS_APPKIT:
        if not _nspasteboard_write('ut16', utf16_bytes):
            print('ERROR: NSPasteboard write failed.', file=sys.stderr)
            sys.exit(1)
    else:
        hex_data = utf16_bytes.hex()
        script = f'set the clipboard to \u00abdata ut16{hex_data}\u00bb'
        result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True)
        if result.returncode != 0:
            print(f'ERROR: {result.stderr.strip()}', file=sys.stderr)
            sys.exit(1)

    print(f'Clipboard ready \u2192 {input_path} as ut16 (Menu)', file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description='Read/write FileMaker fmxmlsnippets via the macOS clipboard',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=f'FM class codes: {", ".join(f"{k}={v}" for k, v in FM_CLASSES.items())}'
    )
    sub = parser.add_subparsers(dest='cmd')

    rp = sub.add_parser('read', help='Read FM objects from clipboard as XML')
    rp.add_argument('output', nargs='?', help='Output file path (default: stdout)')

    wp = sub.add_parser('write', help='Write XML file to clipboard as FM objects')
    wp.add_argument('input', help='fmxmlsnippet XML file to send to clipboard')
    wp.add_argument(
        '--class', dest='cls', default=None,
        help='FM class override (default: auto-detect from XML content). Use "ut16" for menu objects.'
    )

    args = parser.parse_args()

    if args.cmd == 'read':
        read_from_clipboard(args.output)
    elif args.cmd == 'write':
        write_to_clipboard(args.input, args.cls)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
