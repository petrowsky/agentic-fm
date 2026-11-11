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
import json
import os
import re
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
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
    'Group':             'XMSC',
    'CustomFunction':    'XMFN',
    'Field':             'XMFD',
    'BaseTable':         'XMTB',
    'ValueList':         'XMVL',
    'Layout':            'XML2',
    'LayoutObject':      'XML2',
    'Theme':             'XMTH',
    'CustomMenu':        'ut16',
    'CustomMenuSet':     'ut16',
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

_WINDOWS_FORMATS = {
    'XMSS': 'Mac-XMSS',
    'XMSC': 'Mac-XMSC',
    'XML2': 'Mac-XML2',
    'XMLO': 'Mac-XMLO',
    'XMFD': 'Mac-XMFD',
    'XMFN': 'Mac-XMFN',
    'XMTB': 'Mac-XMTB',
    'XMVL': 'Mac-XMVL',
    'XMTH': 'Mac-XMTH',
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
    # Menu elements must be checked before 'Step' — menu XML files contain <Step>
    # elements inside their action blocks, which would otherwise match XMSS first.
    for element in ('CustomMenuSet', 'CustomMenu'):
        if re.search(rf'<{element}[\s>/]', xml_text):
            return 'ut16'
    for element, cls in XML_ELEMENT_TO_CLASS.items():
        if element in ('CustomMenuSet', 'CustomMenu'):
            continue
        if re.search(rf'<{element}[\s>/]', xml_text):
            return cls
    return 'XMSS'  # safe default for steps-only snippets


def read_from_clipboard(output_path=None):
    """Extract FM objects from clipboard and output as formatted XML."""
    if sys.platform == 'win32':
        return _windows_clipboard_read(output_path)

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


def _windows_clipboard_read(output_path=None):
    """Read FileMaker descriptor data using the native Windows clipboard API."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EnumClipboardFormats.argtypes = [wintypes.UINT]
    user32.EnumClipboardFormats.restype = wintypes.UINT
    user32.GetClipboardFormatNameW.argtypes = [wintypes.UINT, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClipboardFormatNameW.restype = ctypes.c_int
    user32.GetClipboardData.argtypes = [wintypes.UINT]
    user32.GetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalSize.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalSize.restype = ctypes.c_size_t
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]

    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError(ctypes.get_last_error(), 'Could not open Windows clipboard')

    try:
        clipboard_format = 0
        format_name = ''
        available_formats = []
        current = 0
        while True:
            current = user32.EnumClipboardFormats(current)
            if not current:
                break
            buffer = ctypes.create_unicode_buffer(128)
            if user32.GetClipboardFormatNameW(current, buffer, len(buffer)):
                available_formats.append(buffer.value)
                if buffer.value.startswith('Mac-XM') or buffer.value == 'Mac-XML2':
                    clipboard_format = current
                    format_name = buffer.value
                    break

        if not clipboard_format:
            details = ', '.join(available_formats) if available_formats else '(only standard Windows formats)'
            print(
                'ERROR: No FileMaker objects found on clipboard.\n'
                f'Available custom formats: {details}',
                file=sys.stderr,
            )
            sys.exit(1)

        memory = user32.GetClipboardData(clipboard_format)
        size = kernel32.GlobalSize(memory)
        pointer = kernel32.GlobalLock(memory)
        if not pointer or size <= 4:
            raise OSError(ctypes.get_last_error(), 'Could not read FileMaker clipboard data')
        try:
            payload = ctypes.string_at(pointer, size)
        finally:
            kernel32.GlobalUnlock(memory)
    finally:
        user32.CloseClipboard()

    declared_size = struct.unpack('<I', payload[:4])[0]
    xml_bytes = payload[4:4 + declared_size]
    xml = xml_bytes.decode('utf-8')

    if output_path:
        with open(output_path, 'w', encoding='utf-8') as file:
            file.write(xml)
        print(f'Saved {format_name} to {output_path}', file=sys.stderr)
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

    if cls not in FM_CLASSES and cls not in UT16_CLASSES:
        print(f"ERROR: Unknown class '{cls}'. Valid options: {', '.join(FM_CLASSES)}", file=sys.stderr)
        sys.exit(1)

    if sys.platform == 'win32':
        _windows_clipboard_write(cls, raw_bytes, xml_text)
        print(f'Clipboard ready \u2192 {input_path} as {cls}', file=sys.stderr)
        return

    if sys.platform != 'darwin':
        _write_via_companion(xml_text, input_path)
        return

    if cls in UT16_CLASSES:
        _write_ut16_to_clipboard(xml_text, input_path)
        return

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


def _windows_clipboard_write(cls, raw_bytes, xml_text):
    """Write FileMaker descriptor data using the native Windows clipboard API."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL('user32', use_last_error=True)
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)

    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.RegisterClipboardFormatW.argtypes = [wintypes.LPCWSTR]
    user32.RegisterClipboardFormatW.restype = wintypes.UINT
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = wintypes.LPVOID
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    if cls in UT16_CLASSES:
        clipboard_format = 13  # CF_UNICODETEXT
        payload = xml_text.encode('utf-16-le') + b'\x00\x00'
    else:
        format_name = _WINDOWS_FORMATS[cls]
        clipboard_format = user32.RegisterClipboardFormatW(format_name)
        if not clipboard_format:
            raise OSError(ctypes.get_last_error(), f'Could not register {format_name}')
        xml_clean = re.sub(r'<\?xml[^?]*\?>\s*', '', xml_text, count=1)
        xml_bytes = xml_clean.encode('utf-8')
        payload = struct.pack('<I', len(xml_bytes)) + xml_bytes

    for _ in range(20):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError(ctypes.get_last_error(), 'Could not open Windows clipboard')

    memory = None
    try:
        if not user32.EmptyClipboard():
            raise OSError(ctypes.get_last_error(), 'Could not empty Windows clipboard')

        memory = kernel32.GlobalAlloc(0x0002, len(payload))  # GMEM_MOVEABLE
        if not memory:
            raise OSError(ctypes.get_last_error(), 'Could not allocate clipboard memory')

        pointer = kernel32.GlobalLock(memory)
        if not pointer:
            raise OSError(ctypes.get_last_error(), 'Could not lock clipboard memory')
        try:
            ctypes.memmove(pointer, payload, len(payload))
        finally:
            kernel32.GlobalUnlock(memory)

        if not user32.SetClipboardData(clipboard_format, memory):
            raise OSError(ctypes.get_last_error(), 'Could not set FileMaker clipboard data')
        memory = None  # Clipboard owns the allocation after SetClipboardData succeeds.
    finally:
        user32.CloseClipboard()
        if memory:
            kernel32.GlobalFree(memory)


def _write_via_companion(xml_text, input_path):
    """Delegate clipboard writes from non-macOS hosts to the macOS companion."""
    configured = os.environ.get('AGENTIC_FM_COMPANION_URL', '').rstrip('/')
    candidates = [
        configured,
        'http://local.hub:8765',
        'http://host.docker.internal:8765',
    ]
    payload = json.dumps({'xml': xml_text}).encode('utf-8')
    errors = []

    for base_url in dict.fromkeys(url for url in candidates if url):
        request = urllib.request.Request(
            f'{base_url}/clipboard',
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.loads(response.read().decode('utf-8'))
            if result.get('success'):
                print(f'Clipboard ready via {base_url} \u2192 {input_path}', file=sys.stderr)
                return
            errors.append(f'{base_url}: {result.get("error", "unknown error")}')
        except (urllib.error.URLError, TimeoutError, ValueError) as exc:
            errors.append(f'{base_url}: {exc}')

    print(
        'ERROR: No usable macOS companion server was reachable. '
        'Set AGENTIC_FM_COMPANION_URL to the companion base URL.\n'
        + '\n'.join(errors),
        file=sys.stderr,
    )
    sys.exit(1)


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
