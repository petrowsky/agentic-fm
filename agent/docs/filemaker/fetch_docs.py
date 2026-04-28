#!/usr/bin/env python3
from __future__ import annotations
"""
Fetch FileMaker Pro reference documentation from the Claris help site.

Downloads script-step, function, and error-code reference pages and
converts them to clean Markdown suitable for AI consumption.  Only
parameters, options, compatibility, and behavioral notes are retained;
examples and boilerplate prose are stripped.

Usage
-----
    python fetch_docs.py              # fetch everything
    python fetch_docs.py --steps      # script steps only
    python fetch_docs.py --functions   # functions only
    python fetch_docs.py --errors     # error codes only

Outputs
-------
    script-steps/<slug>.md
    functions/<category>/<slug>.md
    error-codes.md

Re-running the script skips pages that already have a local .md file.
Delete a file (or pass --force) to re-fetch.

Legal Notice
------------
The content fetched and stored by this script is sourced from the Claris
help site (https://help.claris.com) and is copyright © Claris International
Inc. All rights reserved.

The generated Markdown files are NOT part of this project's Apache 2.0
licensed source code and are intentionally excluded from version control
(see .gitignore).  You may run this script to generate a local copy for
your own personal, non-commercial use in accordance with the Claris
Website Terms of Use (https://claris.com/company/legal/terms).

Do not commit, redistribute, or publish the generated files.
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import urljoin
from xml.etree import ElementTree as ET

# ── Auto-install dependencies ────────────────────────────────────────────
try:
    import requests
    from bs4 import BeautifulSoup, Tag, NavigableString
except ImportError:
    import subprocess as _sp
    _sp.check_call(
        [sys.executable, "-m", "pip", "install", "requests", "beautifulsoup4"]
    )
    import requests
    from bs4 import BeautifulSoup, Tag, NavigableString


# ── Constants ────────────────────────────────────────────────────────────

BASE_URL = "https://help.claris.com/en/pro-help/content/"
HERE = Path(__file__).resolve().parent
STEPS_OUT = HERE / "script-steps"
FUNCS_OUT = HERE / "functions"
SNIPPETS = HERE.parent.parent / "snippet_examples" / "steps"

DELAY = 0.35  # seconds between HTTP requests

# Script-step category page slugs
STEP_CATS = [
    "control-script-steps",
    "navigation-script-steps",
    "editing-script-steps",
    "fields-script-steps",
    "records-script-steps",
    "found-sets-script-steps",
    "windows-script-steps",
    "files-script-steps",
    "accounts-script-steps",
    "artificial-intelligence-script-steps",
    "spelling-script-steps",
    "open-menu-item-script-steps",
    "miscellaneous-script-steps",
]

# Function category page slugs
FUNC_CATS = [
    "text-functions",
    "text-formatting-functions",
    "number-functions",
    "date-functions",
    "time-functions",
    "timestamp-functions",
    "container-functions",
    "japanese-functions",
    "json-functions",
    "aggregate-functions",
    "repeating-functions",
    "financial-functions",
    "trigonometric-functions",
    "logical-functions",
    "artificial-intelligence-functions",
    "miscellaneous-functions",
    "get-functions",
    "design-functions",
    "mobile-functions",
]

# Slugs that are index / category pages, not individual reference pages
_INDEX_SLUGS = (
    {"script-steps-reference", "functions-reference", "scripts",
     "formulas", "functions", "error-codes", "using-variables",
     "repeating-fields", "scripts"}
    | set(STEP_CATS)
    | set(FUNC_CATS)
)

# Known slug overrides  (step name → correct slug on Claris help site)
_SLUG_OVERRIDES = {
    "#": "comment",
    "Configure NFC Reading": "configure-nfc",
    "Perform AppleScript": "perform-applescript-os-x",
    "Speak": "speak-os-x",
    "Perform Script on Server with Callback": "perform-script-on-server-callback",
    "Get Folder Path": "get-directory",
    "Open Upload to Host": "upload-to-server",
    "Send DDE Execute": "send-dde-execute-windows",
    "Open Settings": "open-preferences",
    "If": "if-script-step",
}


# ── HTTP helper ──────────────────────────────────────────────────────────

_session = requests.Session()
_session.headers["User-Agent"] = "FileMaker-DocFetcher/1.0"


def _get(url: str) -> requests.Response:
    time.sleep(DELAY)
    return _session.get(url, timeout=30)


# ── HTML → Markdown conversion ───────────────────────────────────────────

def _inline(tag) -> str:
    """Recursively render a tag to inline Markdown."""
    if isinstance(tag, NavigableString):
        return str(tag)
    if not isinstance(tag, Tag):
        return ""
    parts = [_inline(ch) for ch in tag.children]
    inner = "".join(parts)
    n = tag.name
    if n in ("b", "strong"):
        return f"**{inner.strip()}**"
    if n in ("i", "em"):
        return f"*{inner.strip()}*"
    if n == "code":
        return f"`{tag.get_text()}`"
    if n == "a":
        href = tag.get("href", "")
        if href.startswith("javascript:"):
            return inner
        return f"[{inner}]({urljoin(BASE_URL, href)})"
    if n == "br":
        return "\n"
    return inner


def _table_md(tbl: Tag) -> str:
    """Convert an HTML <table> to a Markdown table."""
    rows: list[list[str]] = []
    for tr in tbl.find_all("tr"):
        cells = [
            _inline(c).strip().replace("|", "\\|")
            for c in tr.find_all(["th", "td"])
        ]
        rows.append(cells)
    if not rows:
        return ""
    ncols = max(len(r) for r in rows)
    for r in rows:
        r.extend([""] * (ncols - len(r)))
    lines = [
        "| " + " | ".join(rows[0]) + " |",
        "| " + " | ".join("---" for _ in range(ncols)) + " |",
    ]
    for r in rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n".join(lines)


def _list_md(tag: Tag) -> str:
    """Convert a <ul> or <ol> to Markdown."""
    items = []
    ordered = tag.name == "ol"
    for i, li in enumerate(tag.find_all("li", recursive=False), 1):
        pfx = f"{i}. " if ordered else "- "
        items.append(pfx + _inline(li).strip())
    return "\n".join(items)


# Section headings to skip (matched as prefix, case-insensitive)
_SKIP_HEADINGS = {"example", "related topic", "see also"}


def _process_element(el: Tag, parts: list[str], skip_sections: set,
                     *, keep_examples: bool = False) -> bool:
    """Process a single element; return True if currently skipping."""
    skip = False

    if el.name in ("h1", "h2", "h3", "h4"):
        txt = el.get_text(strip=True).replace("\xa0", " ")
        norm = re.sub(r"\s*\d+\s*$", "", txt).lower().strip()
        if not keep_examples and any(
            norm.startswith(s) for s in skip_sections
        ):
            return True  # start skipping
        level = int(el.name[1])
        parts.append(f"\n{'#' * level} {txt}\n")
        return False

    if el.name == "table":
        parts.append(_table_md(el) + "\n")
        return False

    if el.name in ("ul", "ol"):
        parts.append(_list_md(el) + "\n")
        return False

    if el.name == "pre":
        parts.append(f"\n```\n{el.get_text()}\n```\n")
        return False

    if el.name in ("p",):
        t = _inline(el).strip()
        if t:
            parts.append(t + "\n")
        return False

    # For divs that wrap content (like compat-wrapper), recurse into children
    if el.name in ("div", "section"):
        # Check for compatibility wrapper (contains a table)
        tbl = el.find("table")
        if tbl:
            heading_el = el.find(["h2", "h3"])
            if heading_el:
                txt = heading_el.get_text(strip=True).replace("\xa0", " ")
                level = int(heading_el.name[1])
                parts.append(f"\n{'#' * level} {txt}\n")
            parts.append(_table_md(tbl) + "\n")
            return False
        # Otherwise recurse
        for child in el.children:
            if isinstance(child, Tag):
                _process_element(child, parts, skip_sections,
                                 keep_examples=keep_examples)
        return False

    return False


def to_markdown(soup: BeautifulSoup, *, keep_examples: bool = False) -> str:
    """Extract the main content of a Claris help page as Markdown.

    Strips navigation, header/footer chrome, examples, and related-topics.
    Keeps: title, Options, Compatibility, Description, Format, Notes.
    """
    # Find the actual content container
    body = (
        soup.find("div", id="mc-main-content")
        or soup.find("div", class_="body-container")
        or soup.find("main")
        or soup.body
        or soup
    )

    parts: list[str] = []
    skip = False

    for el in body.children:
        if not isinstance(el, Tag):
            continue

        if el.name in ("h1", "h2", "h3", "h4"):
            txt = el.get_text(strip=True).replace("\xa0", " ")
            norm = re.sub(r"\s*\d+\s*$", "", txt).lower().strip()
            if not keep_examples and any(
                norm.startswith(s) for s in _SKIP_HEADINGS
            ):
                skip = True
                continue
            skip = False
            level = int(el.name[1])
            parts.append(f"\n{'#' * level} {txt}\n")
            continue

        if skip:
            continue

        _process_element(el, parts, _SKIP_HEADINGS,
                         keep_examples=keep_examples)

    md = "\n".join(parts).strip()
    # Strip common footer boilerplate
    md = re.sub(
        r"Was this topic helpful\?.*$", "", md, flags=re.DOTALL
    ).strip()
    return re.sub(r"\n{3,}", "\n\n", md)


# ── Link discovery ───────────────────────────────────────────────────────

def _discover_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """Return {slug: full_url} for same-directory .html links in *soup*."""
    found: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].split("#")[0].split("?")[0]
        if not href.endswith(".html") or "/" in href:
            continue
        slug = href.replace(".html", "")
        if slug in _INDEX_SLUGS:
            continue
        found[slug] = urljoin(base_url, href)
    return found


# ── Step-name → slug derivation ──────────────────────────────────────────

def _step_name_to_slug(name: str) -> str:
    """Derive the expected Claris help URL slug from a FileMaker step name."""
    # Check both the raw name and the stripped name against overrides
    if name in _SLUG_OVERRIDES:
        return _SLUG_OVERRIDES[name]
    stripped = name.strip()
    if stripped in _SLUG_OVERRIDES:
        return _SLUG_OVERRIDES[stripped]
    slug = name.lower()
    slug = slug.replace("/", "-").replace("\\", "-")
    slug = re.sub(r"[#()\[\]{},.'\"!?;:]", "", slug)
    slug = slug.strip().replace(" ", "-")
    slug = re.sub(r"-{2,}", "-", slug)
    return slug.strip("-")


def _read_step_name(xml_path: Path) -> str | None:
    """Read the name= attribute from a snippet XML file.

    Handles files with trailing comments after </fmxmlsnippet> by
    parsing only up to the closing root tag.
    """
    try:
        text = xml_path.read_text(encoding="utf-8")
        # Strip anything after the closing root tag so ET doesn't choke
        idx = text.find("</fmxmlsnippet>")
        if idx != -1:
            text = text[: idx + len("</fmxmlsnippet>")]
        root = ET.fromstring(text)
        step = root.find("Step")
        if step is not None:
            return step.get("name")
    except Exception:
        pass
    return None


# ── Fetch-and-save helper ────────────────────────────────────────────────

def _fetch_page(url: str, out_path: Path, *, keep_examples=False) -> bool:
    """Download one page, convert to MD, save.  Returns True on success."""
    r = _get(url)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    md = to_markdown(soup, keep_examples=keep_examples)
    if not md.strip():
        return False
    out_path.write_text(md, encoding="utf-8")
    return True


# ── Script Steps ─────────────────────────────────────────────────────────

def fetch_steps(*, force: bool = False) -> dict[str, str]:
    """Discover and download all individual script-step pages.

    Returns {slug: url} of all discovered pages.
    """
    STEPS_OUT.mkdir(parents=True, exist_ok=True)
    slugs: dict[str, str] = {}  # slug → url

    # 1. Discover from category pages (in case any expose step links)
    for cat in STEP_CATS:
        url = BASE_URL + cat + ".html"
        print(f"  Scanning {cat} ... ", end="", flush=True)
        r = _get(url)
        if r.status_code != 200:
            print("SKIP")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        found = _discover_links(soup, url)
        slugs.update(found)
        print(f"{len(found)} links")

    # 2. Supplement with slugs derived from XML filenames
    xml_derived = 0
    if SNIPPETS.is_dir():
        for xml_file in sorted(SNIPPETS.rglob("*.xml")):
            name = _read_step_name(xml_file) or xml_file.stem
            slug = _step_name_to_slug(name)
            if slug and slug not in slugs:
                slugs[slug] = BASE_URL + slug + ".html"
                xml_derived += 1
    print(f"  + {xml_derived} slugs derived from XML filenames")

    # 3. Fetch pages
    total = len(slugs)
    ok = skip = 0
    failed: list[str] = []
    print(f"\n  Fetching {total} script-step pages ...")
    for i, (slug, url) in enumerate(sorted(slugs.items()), 1):
        out = STEPS_OUT / f"{slug}.md"
        if out.exists() and not force:
            print(f"    [{i}/{total}] {slug} (cached)")
            skip += 1
            continue
        if _fetch_page(url, out):
            print(f"    [{i}/{total}] {slug} OK")
            ok += 1
        else:
            print(f"    [{i}/{total}] {slug} -- 404/empty")
            failed.append(slug)

    print(f"\n  Steps: {ok} fetched, {skip} cached, {len(failed)} missing")
    if failed:
        print(f"  Missing: {', '.join(failed)}")
    return slugs


# ── Functions ────────────────────────────────────────────────────────────

def fetch_functions(*, force: bool = False) -> dict[str, dict]:
    """Discover and download all individual function pages.

    Returns {slug: {url, category}}.
    """
    FUNCS_OUT.mkdir(parents=True, exist_ok=True)
    pages: dict[str, dict] = {}

    for cat in FUNC_CATS:
        url = BASE_URL + cat + ".html"
        cat_name = cat.replace("-functions", "")
        print(f"  Scanning {cat} ... ", end="", flush=True)
        r = _get(url)
        if r.status_code != 200:
            print("SKIP")
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        found = _discover_links(soup, url)
        for slug, u in found.items():
            pages[slug] = {"url": u, "category": cat_name}
        print(f"{len(found)} functions")

    total = len(pages)
    ok = skip = 0
    failed: list[str] = []
    print(f"\n  Fetching {total} function pages ...")
    for i, (slug, info) in enumerate(sorted(pages.items()), 1):
        d = FUNCS_OUT / info["category"]
        d.mkdir(exist_ok=True)
        out = d / f"{slug}.md"
        if out.exists() and not force:
            print(f"    [{i}/{total}] {slug} (cached)")
            skip += 1
            continue
        if _fetch_page(url=info["url"], out_path=out):
            print(f"    [{i}/{total}] {slug} OK")
            ok += 1
        else:
            print(f"    [{i}/{total}] {slug} -- 404/empty")
            failed.append(slug)

    print(f"\n  Functions: {ok} fetched, {skip} cached, {len(failed)} missing")
    if failed:
        print(f"  Missing: {', '.join(failed)}")
    return pages


# ── Error Codes ──────────────────────────────────────────────────────────

def fetch_errors(*, force: bool = False):
    out = HERE / "error-codes.md"
    if out.exists() and not force:
        print("  error-codes.md (cached)")
        return
    print("  Fetching error codes ... ", end="", flush=True)
    if _fetch_page(BASE_URL + "error-codes.html", out, keep_examples=True):
        print("OK")
    else:
        print("FAILED")


# ── Cross-reference report ───────────────────────────────────────────────

def cross_reference(step_slugs: dict[str, str]):
    """Compare XML files against discovered doc slugs and report gaps."""
    if not SNIPPETS.is_dir():
        print("  Snippet directory not found; skipping cross-reference.")
        return

    # Build slug → xml-path mapping
    xml_slugs: dict[str, Path] = {}
    for xml_file in sorted(SNIPPETS.rglob("*.xml")):
        name = _read_step_name(xml_file) or xml_file.stem
        slug = _step_name_to_slug(name)
        xml_slugs[slug] = xml_file

    # Slugs that have a doc but no XML file
    doc_files = {p.stem for p in STEPS_OUT.glob("*.md")}
    docs_only = doc_files - set(xml_slugs.keys())
    # Slugs that have an XML file but no doc
    xml_only = set(xml_slugs.keys()) - doc_files

    if docs_only:
        print("\n  Doc pages with no matching XML file:")
        for s in sorted(docs_only):
            print(f"    - {s}")
    if xml_only:
        print("\n  XML files with no matching doc page:")
        for s in sorted(xml_only):
            print(f"    - {s}  ({xml_slugs[s].relative_to(SNIPPETS)})")
    if not docs_only and not xml_only:
        print("\n  All XML files matched to doc pages.")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Fetch FileMaker reference documentation"
    )
    ap.add_argument("--steps", action="store_true", help="Fetch script steps")
    ap.add_argument("--functions", action="store_true", help="Fetch functions")
    ap.add_argument("--errors", action="store_true", help="Fetch error codes")
    ap.add_argument("--all", action="store_true", help="Fetch everything")
    ap.add_argument(
        "--force", action="store_true",
        help="Re-download even if .md already exists",
    )
    args = ap.parse_args()

    if not (args.steps or args.functions or args.errors):
        args.all = True

    step_slugs: dict[str, str] = {}

    if args.all or args.steps:
        print("== Script Steps ==")
        step_slugs = fetch_steps(force=args.force)
        print()

    if args.all or args.functions:
        print("== Functions ==")
        fetch_functions(force=args.force)
        print()

    if args.all or args.errors:
        print("== Error Codes ==")
        fetch_errors(force=args.force)
        print()

    if step_slugs:
        print("== Cross-Reference ==")
        cross_reference(step_slugs)
        print()

    print("Done.")


if __name__ == "__main__":
    main()
