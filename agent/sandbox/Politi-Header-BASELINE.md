# Politi header — locked restore point

**Trigger phrase:** `Gå tillbaka`  
**Locked version:** `v2026-08-08-test-bottom-blend`

When the developer says **Gå tillbaka**, restore from the locked copies:

1. Copy `Politi-HeaderHTML.locked.html` → `Politi-HeaderHTML.html`
2. Copy `Politi-Header-WebViewer.calc.locked.txt` → `Politi-Header-WebViewer.calc.txt`
3. Put HTML on clipboard for paste into `Helicop::HeaderHTML`

## Visual (locked)
- Soft blend heli ↔ dark bg (left + bottom vignette)
- Heli: `cover` + `scale(1.3)`, position `64% 58%`
- Politiet logo + staplar ~30% larger than pre-zoom
- Lime buttons vertically centered; grey sub-buttons under active parent

## FileMaker
- WV object: **`ww_header`**
- Height: **200** pt (or as laid out)
- Fill: `#0e141c` when used as opaque header

## Known limitation — bottom edge
HTML/CSS could not eliminate the visible bottom edge inside the WV paint box in practice.
**Current workaround:** extend/drag the Web Viewer down with no (or transparent) background and send it to the **back**, so content below shows through while the header UI stays on top visually.

If a cleaner in-HTML fix appears later (true transparent WV chrome, different asset crop, etc.), revisit — do not break this locked look without asking.

## Files
| Role | File |
|------|------|
| Working HTML | `Politi-HeaderHTML.html` |
| Locked HTML | `Politi-HeaderHTML.locked.html` |
| Working calc | `Politi-Header-WebViewer.calc.txt` |
| Locked calc | `Politi-Header-WebViewer.calc.locked.txt` |

## Research notes (bottom edge) — 2026-08-08

### What is NOT solvable in pure HTML/CSS
FileMaker Web Viewer is an opaque OS web view (WebKit/WebView2). True transparency through to layout objects behind it is **not** a native capability (Claris Community consensus). CSS `transparent` / `rgba(...,0)` does not punch through the WV chrome.

MBS `WebView.SetDrawsBackground` exists on macOS/iOS only and is historically unreliable across FM versions — not a portable “correct” fix for Helicop.

### Documented FM causes of a bottom line/gap (check these first)
In **Web Viewer Setup** (right-click WV → Web Viewer Setup / Inspector):

1. Uncheck **Display progress bar** — known to draw a persistent bottom line (esp. Windows; reported on Mac too).
2. Uncheck **Display status messages** — known to reserve a bottom “footer” strip the HTML cannot paint into.
3. Appearance: **Line = None**, **Padding = 0**, corner radius 0.

Community threads: “annoying black line at the bottom of a webviewer” (progress bar); “footer that wont go away” (status messages).

### Correct opaque integration (if not using send-to-back)
- Match **WV Appearance Fill** + HTML `background` to the **exact** hex of the FM band under the seam (list/column header may not be `#0e141c`).
- Prefer `height:100%` fill of the WV over a hardcoded `max-height:200px` that can fight a resized WV.
- Optional: let the WV **own** the dark band including the column-header row so the HTML/FM boundary is not at the visual seam.

### Current workaround (valid)
Extend WV downward, Fill none, send to **back** so FM paints the band; HTML UI sits where needed. Accept this when see-through is required.
