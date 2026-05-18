# Window Enumerations

These values apply to steps that support opening in a new window, including **Go to List of Records**, **Go to Related Record**, **New Window**, and others.

## NewWndStyles Attributes

Used when `ShowInNewWindow state="True"`.

| Attribute | Values | Notes |
|---|---|---|
| `Style` | *(see Window Style Types below)* | Window style type |
| `Close` | `"Yes"`, `"No"` | Allow close button |
| `Minimize` | `"Yes"`, `"No"` | Allow minimize button |
| `Maximize` | `"Yes"`, `"No"` | Allow maximize button |
| `Resize` | `"Yes"`, `"No"` | Allow window resize |
| `DimParentWindow` | `"Yes"`, `"No"` | Dim parent window (only with ShowInNewWindow) |
| `Toolbars` | `"Yes"`, `"No"` | Show toolbars (only with ShowInNewWindow) |
| `MenuBar` | `"Yes"`, `"No"` | Show menu bar (only with ShowInNewWindow) |
| `Styles` | numeric bitmask | Internal style flags |

## Window Style Types

Attribute: `NewWndStyles/@Style`

| HR Label | XML Value | Supported Attributes |
|---|---|---|
| Document | `Document` | Close, Minimize, Maximize, Resize, Toolbars, MenuBar |
| Floating Document | `Floating` | Close, Minimize, Maximize, Resize, Toolbars, MenuBar |
| Dialog | `Dialog` | Close, Maximize, Resize, Toolbars, MenuBar *(no Minimize, no DimParentWindow)* |
| Card | `Card` | Close, DimParentWindow only *(no Minimize, Maximize, Resize, Toolbars, MenuBar)* |

Note: Document and Floating Document do not support DimParentWindow. Only Card supports DimParentWindow.

## New Window Params

When `ShowInNewWindow state="True"` (or on the standalone `New Window` step), these sibling calc elements **may** appear. All five are optional. The standard `TMPL_NewScript - Transactions` template form emits only `Name`, `Height`, and `Width`; `DistanceFromTop` and `DistanceFromLeft` are omitted so FileMaker uses default placement.

| Element | Notes |
|---|---|
| `Name/Calculation` | Window name |
| `Height/Calculation` | Window height |
| `Width/Calculation` | Window width |
| `DistanceFromTop/Calculation` | Top position |
| `DistanceFromLeft/Calculation` | Left position |
