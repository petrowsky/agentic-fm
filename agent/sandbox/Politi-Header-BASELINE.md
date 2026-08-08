# Politi header — locked restore point

**Trigger phrase:** `Gå tillbaka`  
**Locked version:** `v2026-08-08-test-fill-scale`

When the developer says **Gå tillbaka**, restore from the locked copies (do not invent an older look):

1. Copy `Politi-HeaderHTML.locked.html` → `Politi-HeaderHTML.html`
2. Copy `Politi-Header-WebViewer.calc.locked.txt` → `Politi-Header-WebViewer.calc.txt`
3. Put HTML on clipboard for paste into `Helicop::HeaderHTML`

## Visual (locked)
- Soft blend heli ↔ dark bg
- Heli: `background-size:cover` + `transform:scale(1.3)` (~30% zoom, full box fill)
- Politiet logo + staplar at ~30% larger than pre-zoom baseline
- Lime main buttons vertically centered
- Grey sub-buttons centered under active parent
- Full 200 px WV target

## FileMaker
- WV object: **`ww_header`**
- Height: **200** pt
- Fill: `#0e141c`

## Files
| Role | File |
|------|------|
| Working HTML | `Politi-HeaderHTML.html` |
| Locked HTML | `Politi-HeaderHTML.locked.html` |
| Working calc | `Politi-Header-WebViewer.calc.txt` |
| Locked calc | `Politi-Header-WebViewer.calc.locked.txt` |
