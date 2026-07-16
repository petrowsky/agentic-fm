#!/usr/bin/env python3
"""Regenerate AvinOps dashboard .calc.txt files from HTML sources."""

import re
from pathlib import Path

SANDBOX = Path(__file__).resolve().parent

DASHBOARDS = [
    "AvinOps-Bases-Dashboard",
    "AvinOps-Pilot-Dashboard",
    "AvinOps-HTC-Dashboard",
    "AvinOps-HelicopterType-Dashboard",
]

NO_CAROUSEL = {
    "AvinOps-Bases-Dashboard",
    "AvinOps-Pilot-Dashboard",
    "AvinOps-HTC-Dashboard",
    "AvinOps-HelicopterType-Dashboard",
}

CALC_TEMPLATE = """Let ( [
\t~json = AM_Dashboard_Cache::DashboardJSON ;
\t~updated = GetAsText ( AM_Dashboard_Cache::LastUpdated_ts ) ;
\t~cache = AM_Dashboard_Cache::CacheName ;
\t~twoPhaseFlag = If ( not IsEmpty ( FilterValues ( "PilotStats¶HTCStats¶PilotStats_7D¶PilotStats_30D¶HTCStats_7D¶HTCStats_30D" ; ~cache ) ) or PatternCount ( ~cache ; "PilotStats_" ) or PatternCount ( ~cache ; "HTCStats_" ) ; "1" ; "0" ) ;
\t~isWebFlag = If ( PatternCount ( Get ( ApplicationVersion ) ; "Web" ) > 0 ; "1" ; "0" ) ;
\t~html = "{html}" ;
\t~html = Substitute ( ~html ;
\t\t[ "%%JSON%%" ; ~json ] ;
\t\t[ "%%UPDATED%%" ; ~updated ] ;
\t\t[ "%%CACHE%%" ; ~cache ] ;
\t\t[ "%%TWO_PHASE%%" ; ~twoPhaseFlag ] ;
\t\t[ "%%IS_WEB%%" ; ~isWebFlag ]
\t)
] ;

Case (
\tPatternCount ( Get ( ApplicationVersion ) ; "Web" ) > 0 ;
\t"data:text/html;charset=utf-8," & GetAsURLEncoded ( ~html ) ;
\t~html
)

)"""

CALC_TEMPLATE_NO_CAROUSEL = """Let ( [
\t~json = AM_Dashboard_Cache::DashboardJSON ;
\t~updated = GetAsText ( AM_Dashboard_Cache::LastUpdated_ts ) ;
\t~cache = AM_Dashboard_Cache::CacheName ;
\t~isWebFlag = If ( PatternCount ( Get ( ApplicationVersion ) ; "Web" ) > 0 ; "1" ; "0" ) ;
\t~html = "{html}" ;
\t~html = Substitute ( ~html ;
\t\t[ "%%JSON%%" ; ~json ] ;
\t\t[ "%%UPDATED%%" ; ~updated ] ;
\t\t[ "%%CACHE%%" ; ~cache ] ;
\t\t[ "%%IS_WEB%%" ; ~isWebFlag ]
\t)
] ;

Case (
\tPatternCount ( Get ( ApplicationVersion ) ; "Web" ) > 0 ;
\t"data:text/html;charset=utf-8," & GetAsURLEncoded ( ~html ) ;
\t~html
)

)"""

TWO_PHASE_CSS = """
html.two-phase-scroll,html.two-phase-scroll body{
  height:auto;min-height:100%;overflow-x:hidden;overflow-y:auto;
}
html.two-phase-scroll .wrapper{min-height:100%;height:auto;}
.scroll-spacer{display:none;flex:0 0 auto;}
html.two-phase-scroll .scroll-spacer{display:block;height:72vh;}
"""


def minify_html(html: str) -> str:
    html = html.strip()
    # Strip // comments inside <script> blocks before collapsing newlines —
    # otherwise a // comment eats the rest of the minified one-line script.
    def strip_js_line_comments(match: re.Match) -> str:
        body = match.group(1)
        body = re.sub(r"(^|[^:])//.*?$", r"\1", body, flags=re.MULTILINE)
        return f"<script>{body}</script>"

    html = re.sub(
        r"<script>(.*?)</script>",
        strip_js_line_comments,
        html,
        flags=re.DOTALL | re.IGNORECASE,
    )
    html = re.sub(r">\s+<", "><", html)
    html = re.sub(r"\s+", " ", html)
    return html.strip()


def escape_for_fm_double_quoted(s: str) -> str:
    s = s.replace("\\", "\\\\")
    s = s.replace('"', '\\"')
    return s


def fix_css(html: str) -> str:
    """Repair broken CSS injection and ensure two-phase rules exist."""
    broken = (
        "color:white;overflow:hidden;font-weight:300;html.two-phase-scroll,"
        "html.two-phase-scroll body{height:auto;min-height:100%;overflow-x:hidden;"
        "overflow-y:auto;}html.two-phase-scroll .wrapper{min-height:185vh;height:auto;}"
        "html.two-phase-scroll .grid{min-height:85vh;}"
        "html.two-phase-scroll .chart-wrap{min-height:72vh;}"
        "html.two-phase-scroll .wrapper{min-height:100%;height:auto;}"
        ".scroll-spacer{display:none;flex:0 0 auto;}"
        "html.two-phase-scroll .scroll-spacer{display:block;height:72vh;}"
    )
    fixed = (
        "color:white;overflow:hidden;font-weight:300;\n"
        "}\n"
        + TWO_PHASE_CSS.strip()
    )
    if broken in html:
        html = html.replace(broken, fixed, 1)
    elif "two-phase-scroll" not in html:
        html = html.replace(
            "color:white;overflow:hidden;font-weight:300;\n}",
            fixed,
            1,
        )
    return html


def fix_orphan_fmnav(html: str) -> str:
    """Remove leftover fmNav tail from a partial regex replace."""
    pattern = re.compile(
        r"initTwoPhaseLayout\(\);\s*"
        r"if\(action === 'stop'\)\{stopCarousel\(\);return;\}\s*"
        r"var map=\{\s*"
        r"prev:\['AM Dashboard Previous Record',''\],\s*"
        r"next:\['AM Dashboard Next Record',''\],\s*"
        r"home:\['Go to overview',''\]\s*"
        r"\};\s*"
        r"var item=map\[action\];\s*"
        r"if\(item\)\{fm\(item\[0\],item\[1\]\);\}\s*"
        r"\}\s*",
        re.DOTALL,
    )
    return pattern.sub("initTwoPhaseLayout();\n\n", html, count=1)


def ensure_resume_call(html: str) -> str:
    if re.search(r"\}\);\s*maybeResumeCarousel\(\);\s*</script>\s*</body>", html):
        return html
    return re.sub(
        r"\}\);\s*</script>\s*</body>",
        "});\nmaybeResumeCarousel();\n</script>\n</body>",
        html,
        count=1,
    )


def ensure_carousel_block(html: str) -> str:
    carousel_path = SANDBOX / "_avinops_carousel.js"
    carousel_js = carousel_path.read_text(encoding="utf-8").strip()
    marker = "var _carouselTopMs = 4000;"
    if marker not in html:
        raise ValueError("Carousel block not found")
    if "function maybeResumeCarousel()" in html and "window.__avinopsCarousel" in html:
        return html
    pattern = re.compile(
        r"<script>\s*var _carouselTopMs = 4000;.*?"
        r"(?=const dashboard = %%JSON%%;)",
        re.DOTALL,
    )
    if not pattern.search(html):
        raise ValueError("Could not locate carousel block before dashboard data")
    return pattern.sub("<script>\n" + carousel_js + "\n\n", html, count=1)


def process_html(html: str, name: str) -> str:
    if name in NO_CAROUSEL:
        return html
    html = fix_css(html)
    html = ensure_carousel_block(html)
    html = fix_orphan_fmnav(html)
    html = ensure_resume_call(html)
    return html


def main() -> None:
    for name in DASHBOARDS:
        html_path = SANDBOX / f"{name}.html"
        calc_path = SANDBOX / f"{name}.calc.txt"
        html = html_path.read_text(encoding="utf-8")
        html = process_html(html, name)
        html_path.write_text(html, encoding="utf-8")
        minified = minify_html(html)
        escaped = escape_for_fm_double_quoted(minified)
        template = CALC_TEMPLATE_NO_CAROUSEL if name in NO_CAROUSEL else CALC_TEMPLATE
        calc = template.format(html=escaped)
        calc_path.write_text(calc, encoding="utf-8")
        print(f"Wrote {calc_path.name} ({len(calc)} chars)")


if __name__ == "__main__":
    main()
