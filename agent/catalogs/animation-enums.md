# Animation Enumerations

These animation values apply to steps that perform layout transitions, including **Go to List of Records**, **Go to Layout**, **Go to Related Record**, and others.

Attribute: `Animation/@value`

FileMaker Go only. Not supported on Pro, WebDirect, Server, Cloud, Data API, or CWP.

## Animation Values

| HR Label | XML Value |
|---|---|
| None | *(no Animation element)* |
| Slide in from Left | `SlideFromLeft` |
| Slide in from Right | `SlideFromRight` |
| Slide in from Bottom | `SlideFromBottom` |
| Slide out to Left | `SlideToLeft` |
| Slide out to Right | `SlideToRight` |
| Slide out to Bottom | `SlideToBottom` |
| Flip from Left | `FlipFromLeft` |
| Flip from Right | `FlipFromRight` |
| Zoom In | `ZoomIn` |
| Zoom Out | `ZoomOut` |
| Cross Dissolve | `CrossDissolve` |

## LayoutDestination Values

These values are shared across steps that navigate to a layout.

Attribute: `LayoutDestination/@value`

| HR Label | XML Value | Layout Element |
|---|---|---|
| `<Current Layout>` | `CurrentLayout` | *(none)* |
| `<Original Layout>` | `OriginalLayout` | *(none)* |
| `"Layout Name" (TableOccurrence)` | `SelectedLayout` | `<Layout id="" name=""/>` |
| `"layout_name"` (by calc) | `LayoutNameByCalc` | `<Layout><Calculation>` |
| `1` (by number) | `LayoutNumberByCalc` | `<Layout><Calculation>` |

**`<DistanceFromTop>` and `<DistanceFromLeft>` are independent of `LayoutDestination`.** They are optional positioning siblings for `New Window` and for new-window mode of `Go to List of Records` / `Go to Related Record`. Omit them unless the script needs a specific screen offset — FileMaker uses default placement when they are absent. The standard `TMPL_NewScript - Transactions` template form omits both.
