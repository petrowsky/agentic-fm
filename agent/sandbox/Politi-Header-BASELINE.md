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
