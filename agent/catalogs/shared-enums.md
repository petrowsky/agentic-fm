# Shared Enumerations for Import/Export Steps

These enumerations apply to **Convert File**, **Export Records**, and **Import Records** steps. They appear as attributes within the `<ImportOptions>` and `<Profile>` elements.

## CharacterSet

Attribute: `ImportOptions/@CharacterSet` or `ExportOptions/@CharacterSet`

| XML Value | HR Label (Import) | HR Label (Export) | Description |
|---|---|---|---|
| `Windows` | Windows (ANSI) | Windows (ANSI) | Windows ANSI encoding |
| `DOS` | ASCII (DOS) | ASCII (DOS) | DOS/OEM encoding |
| `Macintosh` | Macintosh | Macintosh | Mac Roman encoding |
| `Unicode` | Unicode (UTF-16, Windows) | Unicode (UTF-16) | UTF-16 Little Endian |
| `UnicodeBE` | Unicode (UTF-16) | *(Export n/a)* | UTF-16 Big Endian (Import only) |
| `UTF-8` | Unicode (UTF-8) | Unicode (UTF-8) | UTF-8 encoding |
| `ShiftJIS` | Japanese (Shift-JIS) | Japanese (Shift-JIS) | Shift JIS (Japanese) |
| `ChineseSimplified` | Simplified Chinese (GB) | Simplified Chinese (GB) | GB2312 / GBK (Simplified Chinese) |
| `ChineseTraditional` | Traditional Chinese (Big-5) | Traditional Chinese (Big-5) | Big5 (Traditional Chinese) |
| `EUC-KR` | Korean (EUC-KR) | Korean (EUC-KR) | EUC-KR (Korean) |

## DataType (File Source Type)

Attribute: `Profile/@DataType`

All values are 4-character codes (classic Mac file type convention). Shorter names are padded with trailing spaces.

| XML Value | HR Label | Description | Extra Profile Attributes |
|---|---|---|---|
| `"    "` (4 spaces) | *(default)* | Default / unspecified | — |
| `"FMPR"` | FileMaker Pro | FileMaker Pro file | `table="0"` |
| `"TABS"` | Tab-Separated Values | Tab-Separated Values | — |
| `"COMS"` | Comma-Separated Values | Comma-Separated Values | — |
| `"MRGE"` | Merge | Merge file | `FieldNameRow="0"` always |
| `"HTML"` | HTML Table | HTML Table (Export only) | — |
| `"XLS "` (trailing space) | *(Import only)* | Excel .xls | `FileName`, `WorksheetName`, `SelectedSheet` |
| `"XLSX"` | Excel Workbooks (.xlsx) | Excel .xlsx | `FileName`, `WorksheetName`, `SelectedSheet` |
| `"DBF "` (trailing space) | DBF | dBASE | — |
| `"XML "` (trailing space) | XML | XML Source | Used with `DataSourceType="XMLSource"` |

## Profile Attributes

| Attribute | Values | Notes |
|---|---|---|
| `FieldDelimiter` | `"&#9;"` (tab), `","` (comma), `";"` (semicolon) | Field separator character |
| `IsPredefined` | `"-1"` (custom/default), `"1"` (predefined format) | Whether format is a predefined type |
| `FieldNameRow` | `"-1"` (no field names), `"0"` (first row has field names) | User-togglable for TABS, COMS, XLS, XLSX. Always `"0"` for MRGE, DBF. Always `"-1"` for FMPR, default. |
| `FileName` | file path string | Only for XLS/XLSX types |
| `WorksheetName` | sheet name string | Only for XLS/XLSX types |
| `SelectedSheet` | sheet index (e.g. `"0"`) | Only for XLS/XLSX types |
| `table` | table index (e.g. `"0"`) | Only for FMPR type |

## ImportOptions Attributes

| Attribute | Values | Notes |
|---|---|---|
| `CharacterSet` | See CharacterSet table above | Source file encoding |
| `PreserveContainer` | `"True"`, `"False"` | Preserve container field data |
| `MatchFieldNames` | `"True"`, `"False"` | Auto-match by field name |
| `AutoEnter` | `"True"`, `"False"` | Perform auto-enter during import |
| `SplitRepetitions` | `"True"`, `"False"` | Split repeating fields |
| `AddRemainingRecords` | `"True"`, `"False"` | Add remaining records as new (only with UpdateOnMatch/Update methods) |
| `method` | See Import Method table below | Import method |

## Import Method

Attribute: `ImportOptions/@method`

| HR Label | XML Value | Description |
|---|---|---|
| Add | `Add` | Add new records to the target table |
| Update | `UpdateOnMatch` | Update matching records (requires at least one Match field in TargetFields) |
| Replace | `Update` | Replace data in current found set |

**Note:** HR "Update" maps to XML `UpdateOnMatch`, and HR "Replace" maps to XML `Update`. This naming is a known FileMaker inconsistency.

## TargetFields

`<TargetFields>` contains `<Field>` elements defining the field mapping for import.

| Attribute | Values | Notes |
|---|---|---|
| `map` | `"Import"`, `"DoNotImport"`, `"Match"` | Import action for this field |
| `FieldOptions` | `"0"`, `"2"` | Field option flags |
| `id` | field ID | Target field ID |
| `name` | field name | Target field name |

- `map="Match"` is only used with `method="UpdateOnMatch"` to identify the key field(s) for matching.

## ExportOptions Attributes (Export Records only)

| Attribute | Values | Notes |
|---|---|---|
| `FormatUsingCurrentLayout` | `"True"`, `"False"` | Apply current layout's data formatting to exported data |
| `CharacterSet` | See CharacterSet table above | Output file character encoding |

## ExportEntries / SummaryFields (Export Records only)

- `<ExportEntries>` contains `<ExportEntry><Field table="" id="" name=""/></ExportEntry>` elements defining the export field order.
- `<SummaryFields>` contains `<Field GroupByFieldIsSelected="True" table="" id="" name=""/>` elements for group-by fields.
- Both are dialog-managed and not directly represented in HR.

## DataSourceType

Attribute: `DataSourceType/@value`

| XML Value | Description |
|---|---|
| `File` | File-based data source (local file path) |
| `Folder` | Folder import (container files or text files) |
| `XMLSource` | XML data with stylesheet (HTTP, file, or calculation) |

### File Source HR Popup Options

When `DataSourceType="File"`, the data source type popup in HR shows:

| HR Label | Profile DataType |
|---|---|
| All Available | *(no Profile element)* |
| FileMaker Pro Files | `FMPR` |
| Custom-Separated Values... | *(custom FieldDelimiter)* |
| Comma-Separated Values | `COMS` |
| Merge Files | `MRGE` |
| Excel 95-2004 Workbooks (xls) | `XLS ` |
| Excel Workbooks (xlsx) | `XLSX` |
| dBase Files | `DBF ` |

### Folder Profile Attributes (Import Records only)

When `DataSourceType="Folder"`, the Profile element has additional attributes:

| Attribute | Values | HR Label | Notes |
|---|---|---|---|
| `ImportByReference` | `"True"`, `"False"` | Import only a reference | Store reference vs embed file |
| `PictureAndMovieImport` | `"True"`, `"False"` | Picture and movie files / Text files | True = images/video, False = text files |
| `IncludeEnclosedFolders` | `"True"`, `"False"` | Include all enclosed folders | Recurse into subfolders |
| `FolderName` | path string | *(child element)* | Source folder path |

DataType for Folder sources is always `"BTCH"`.

### XMLSource Profile Variants (Import Records, Convert File)

When `DataSourceType="XMLSource"`, the Profile has XMLType/XSLType attributes and matching child elements:

| XMLType / XSLType | Child Element | Description |
|---|---|---|
| `XMLFile` / `XSLFile` | `<XMLFile>path</XMLFile>` / `<XSLFile>path</XSLFile>` | Local file paths |
| `XMLHttp` / `XSLHttp` | `<XMLHttp>url</XMLHttp>` / `<XSLHttp>url</XSLHttp>` | HTTP URLs |
| `XMLCalculation` / `XSLCalculation` | `<XMLCalc><Calculation>...</Calculation></XMLCalc>` / `<XSLCalc><Calculation>...</Calculation></XSLCalc>` | Calculations |

XMLType and XSLType can be mixed (e.g., `XMLCalculation` with `XSLHttp`). DataType for XML sources is always `"XML "` (with trailing space).
With no style sheet, XSLType is `NoXSL` and there is no XSL child.
Convert File keeps the File attributes (`FieldDelimiter`, `IsPredefined`, `FieldNameRow`) on an XMLSource Profile, and writes an XML calculation a second time as the step-level `<Calculation>` after the Profile; FileMaker keeps that copy and drops an `<XMLCalc>` pasted without it (FileMaker Pro 26.0.3).
A file child holds one path per line.
