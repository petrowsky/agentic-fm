#!/usr/bin/env bash
#
# fmcontext.sh - Generate AI-optimized context index files from exploded XML
#
# Reads the exploded XML in agent/xml_parsed/ (produced by fmparse.sh) and
# uses xmllint to extract only the signal -- IDs, names, types, references --
# into lightweight pipe-delimited index files in agent/context/{solution}/.
#
# These index files enable the AI to look up FileMaker objects without reading
# verbose XML, dramatically reducing token consumption.
#
# Usage:
#   ./fmcontext.sh                         # regenerate all solutions
#   ./fmcontext.sh -s "Invoice Solution"   # regenerate one solution only
#
# Options:
#   -s, --solution NAME   Process only this solution (default: all found in xml_parsed)
#   -h, --help            Show this help message
#
# Dependencies:
#   - xmllint (ships with macOS via libxml2)
#   - agent/xml_parsed/ must be populated (run fmparse.sh first)
#

set -euo pipefail

# ---------------------------------------------------------------------------
# Output helpers -- all messages go to stdout so FileMaker can capture them
# ---------------------------------------------------------------------------
msg()   { echo "==> $1"; }
error() { echo "ERROR: $1"; exit 1; }

# ---------------------------------------------------------------------------
# Resolve project root relative to this script's location
# ---------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$SCRIPT_DIR"

XML_PARSED_DIR="$PROJECT_ROOT/agent/xml_parsed"
CONTEXT_DIR="$PROJECT_ROOT/agent/context"

SOLUTION_NAME=""

# ---------------------------------------------------------------------------
# Usage / help
# ---------------------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $(basename -- "$0") [-s SOLUTION_NAME]

Generate AI-optimized context index files from the exploded XML in
agent/xml_parsed/. Output is written to agent/context/{solution}/.

Options:
  -s, --solution NAME   Process only this solution (default: all found in xml_parsed)
  -h, --help            Show this help message

Generated files (per solution under agent/context/{solution}/):
  fields.index             All fields across all tables
  relationships.index      Relationship graph (TOs, join fields, cascades)
  layouts.index            Layout names, IDs, and base table occurrences
  scripts.index            Script names, IDs, and folder paths
  table_occurrences.index  Table occurrence to base table mapping
  value_lists.index        Value list names, sources, and values
  custom_functions.index   Custom functions with classification (constant/functional/solution_specific/utility)

Dependencies:
  xmllint must be available (ships with macOS).
  agent/xml_parsed/ must be populated (run fmparse.sh first).
EOF
    exit 0
}

# ---------------------------------------------------------------------------
# Parse arguments
# ---------------------------------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help) usage ;;
        -s|--solution) SOLUTION_NAME="$2"; shift 2 ;;
        *) error "Unknown option '$1'. Run '$(basename -- "$0") --help' for usage." ;;
    esac
done

# ---------------------------------------------------------------------------
# Verify prerequisites
# ---------------------------------------------------------------------------
if ! command -v xmllint &>/dev/null; then
    error "xmllint is not available. On macOS it ships with the system. On Linux, install libxml2-utils."
fi

if [[ ! -d "$XML_PARSED_DIR" ]]; then
    error "agent/xml_parsed/ does not exist. Run fmparse.sh first."
fi

# Quick check that there's content to process
if [[ -z "$(find "$XML_PARSED_DIR" -name '*.xml' -print -quit 2>/dev/null)" ]]; then
    error "agent/xml_parsed/ contains no XML files. Run fmparse.sh first."
fi

# ---------------------------------------------------------------------------
# Helper: extract a string value via xmllint, returning empty string on failure
# ---------------------------------------------------------------------------
xval() {
    local xpath="$1"
    local file="$2"
    xmllint --xpath "$xpath" "$file" 2>/dev/null || echo ""
}

# ---------------------------------------------------------------------------
# Helper: derive folder path from a file path relative to its domain root
#
# Given: /path/to/xml_parsed/script_stubs/Solution Name/Folder A - ID 10/Script - ID 5.xml
# The domain dir is: /path/to/xml_parsed/script_stubs
# Everything between the solution name dir and the filename is the folder path.
# ---------------------------------------------------------------------------
get_folder_path() {
    local file="$1"
    local domain_dir="$2"

    # Get the path relative to the domain dir
    local rel_path="${file#"$domain_dir"/}"

    # Strip the solution-name directory (first path component)
    rel_path="${rel_path#*/}"

    # Get the directory portion (everything except the filename)
    local dir_part
    dir_part="$(dirname -- "$rel_path")"

    if [[ "$dir_part" == "." ]]; then
        echo ""
    else
        # Strip " - ID NNN" suffixes from folder names for readability
        echo "$dir_part" | sed 's/ - ID [0-9]*//g'
    fi
}

# ---------------------------------------------------------------------------
# Ensure parent context directory exists
# ---------------------------------------------------------------------------
mkdir -p "$CONTEXT_DIR"

# ---------------------------------------------------------------------------
# Discover solutions to process
# ---------------------------------------------------------------------------
if [[ -z "$SOLUTION_NAME" ]]; then
    # Avoid `mapfile` — it requires bash 4+, and macOS ships bash 3.2.
    SOLUTIONS=()
    while IFS= read -r _solution_line; do
        SOLUTIONS+=("$_solution_line")
    done < <(
        find "$XML_PARSED_DIR" -mindepth 2 -maxdepth 2 -type d \
            | sed 's|.*/||' | sort -u
    )
    if [[ ${#SOLUTIONS[@]} -eq 0 ]]; then
        error "No solution subfolders found in agent/xml_parsed/. Run fmparse.sh first."
    fi
else
    SOLUTIONS=("$SOLUTION_NAME")
fi

# ---------------------------------------------------------------------------
# Per-solution generation loop
# ---------------------------------------------------------------------------
total_all_lines=0

for SOLUTION in "${SOLUTIONS[@]}"; do
    SOLUTION_CONTEXT_DIR="$CONTEXT_DIR/$SOLUTION"

    # Clear only this solution's subfolder
    if [[ -d "$SOLUTION_CONTEXT_DIR" ]]; then
        find "$SOLUTION_CONTEXT_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
        msg "Cleared agent/context/$SOLUTION/"
    else
        mkdir -p "$SOLUTION_CONTEXT_DIR"
        msg "Created agent/context/$SOLUTION/"
    fi

    msg "Generating index files for: $SOLUTION"

    # ---------------------------------------------------------------------------
    # 1. fields.index
    # ---------------------------------------------------------------------------
    {
        echo "# TableName|TableID|FieldName|FieldID|DataType|FieldType|AutoEnter|Flags"

        if [[ -d "$XML_PARSED_DIR/tables/$SOLUTION" ]]; then
        find "$XML_PARSED_DIR/tables/$SOLUTION" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            table_name=$(xval 'string(/FieldCatalog/BaseTableReference/@name)' "$file")
            table_id=$(xval 'string(/FieldCatalog/BaseTableReference/@id)' "$file")

            field_count=$(xval 'count(//Field)' "$file")
            field_count=${field_count:-0}

            for ((i=1; i<=field_count; i++)); do
                fname=$(xval "string(//Field[$i]/@name)" "$file")
                fid=$(xval "string(//Field[$i]/@id)" "$file")
                dtype=$(xval "string(//Field[$i]/@datatype)" "$file")
                ftype=$(xval "string(//Field[$i]/@fieldtype)" "$file")

                # Auto-enter: prefer the calculation text, fall back to the type attribute
                auto_enter=""
                auto_type=$(xval "string(//Field[$i]/AutoEnter/@type)" "$file")
                if [[ "$auto_type" == "Calculated" ]]; then
                    auto_calc=$(xval "string(//Field[$i]/AutoEnter/Calculated/Calculation/Text)" "$file")
                    # Collapse multi-line calcs to single line (preserve readability)
                    auto_calc=$(echo "$auto_calc" | tr '\n' ' ' | sed 's/  */ /g' | sed 's/^ //;s/ $//')
                    auto_enter="auto:${auto_calc}"
                elif [[ -n "$auto_type" ]]; then
                    auto_enter="auto:${auto_type}"
                fi

                # For calculated fields, extract the calculation formula
                if [[ "$ftype" == "Calculated" && -z "$auto_enter" ]]; then
                    calc_text=$(xval "string(//Field[$i]/Calculation/Text)" "$file")
                    if [[ -n "$calc_text" ]]; then
                        calc_text=$(echo "$calc_text" | tr '\n' ' ' | sed 's/  */ /g' | sed 's/^ //;s/ $//')
                        auto_enter="calc:${calc_text}"
                    fi
                fi

                # Flags: collect validation and storage flags
                flags=""
                not_empty=$(xval "string(//Field[$i]/Validation/@notEmpty)" "$file")
                unique=$(xval "string(//Field[$i]/Validation/@unique)" "$file")
                global=$(xval "string(//Field[$i]/Storage/@global)" "$file")
                stored=$(xval "string(//Field[$i]/Storage/@storeCalculationResults)" "$file")

                [[ "$not_empty" == "True" ]] && flags="${flags}notEmpty,"
                [[ "$unique" == "True" ]] && flags="${flags}unique,"
                [[ "$global" == "True" ]] && flags="${flags}global,"
                [[ "$stored" == "False" ]] && flags="${flags}unstored,"

                # Trim trailing comma
                flags="${flags%,}"

                echo "${table_name}|${table_id}|${fname}|${fid}|${dtype}|${ftype}|${auto_enter}|${flags}"
            done
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/fields.index"

    field_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/fields.index") - 1 ))
    msg "  fields.index: $field_lines fields"

    # ---------------------------------------------------------------------------
    # 2. relationships.index
    # ---------------------------------------------------------------------------
    {
        echo "# LeftTO|LeftTOID|RightTO|RightTOID|JoinType|JoinFields|CascadeCreate|CascadeDelete"

        if [[ -d "$XML_PARSED_DIR/relationships/$SOLUTION" ]]; then
        find "$XML_PARSED_DIR/relationships/$SOLUTION" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            left_to=$(xval 'string(//LeftTable/TableOccurrenceReference/@name)' "$file")
            left_to_id=$(xval 'string(//LeftTable/TableOccurrenceReference/@id)' "$file")
            right_to=$(xval 'string(//RightTable/TableOccurrenceReference/@name)' "$file")
            right_to_id=$(xval 'string(//RightTable/TableOccurrenceReference/@id)' "$file")

            cascade_create=$(xval 'string(//LeftTable/@cascadeCreate)' "$file")
            cascade_delete=$(xval 'string(//LeftTable/@cascadeDelete)' "$file")

            # Handle multiple join predicates
            pred_count=$(xval 'count(//JoinPredicate)' "$file")
            pred_count=${pred_count:-0}
            join_type=""
            join_fields=""

            for ((j=1; j<=pred_count; j++)); do
                jtype=$(xval "string(//JoinPredicate[$j]/@type)" "$file")
                lfield=$(xval "string(//JoinPredicate[$j]/LeftField/FieldReference/@name)" "$file")
                rfield=$(xval "string(//JoinPredicate[$j]/RightField/FieldReference/@name)" "$file")

                if [[ -n "$join_type" ]]; then
                    join_type="${join_type}+${jtype}"
                    join_fields="${join_fields}+${lfield}=${rfield}"
                else
                    join_type="$jtype"
                    join_fields="${lfield}=${rfield}"
                fi
            done

            echo "${left_to}|${left_to_id}|${right_to}|${right_to_id}|${join_type}|${join_fields}|${cascade_create}|${cascade_delete}"
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/relationships.index"

    rel_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/relationships.index") - 1 ))
    msg "  relationships.index: $rel_lines relationships"

    # ---------------------------------------------------------------------------
    # 3. layouts.index
    # ---------------------------------------------------------------------------
    {
        echo "# LayoutName|LayoutID|BaseTOName|BaseTOID|FolderPath"

        if [[ -d "$XML_PARSED_DIR/layouts/$SOLUTION" ]]; then
        find "$XML_PARSED_DIR/layouts/$SOLUTION" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            layout_name=$(xval 'string(/Layout/@name)' "$file")
            layout_id=$(xval 'string(/Layout/@id)' "$file")
            base_to=$(xval 'string(/Layout/TableOccurrenceReference/@name)' "$file")
            base_to_id=$(xval 'string(/Layout/TableOccurrenceReference/@id)' "$file")

            folder_path=$(get_folder_path "$file" "$XML_PARSED_DIR/layouts")

            echo "${layout_name}|${layout_id}|${base_to}|${base_to_id}|${folder_path}"
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/layouts.index"

    layout_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/layouts.index") - 1 ))
    msg "  layouts.index: $layout_lines layouts"

    # ---------------------------------------------------------------------------
    # 4. scripts.index
    # ---------------------------------------------------------------------------
    {
        echo "# ScriptName|ScriptID|FolderPath"

        if [[ -d "$XML_PARSED_DIR/script_stubs/$SOLUTION" ]]; then
        find "$XML_PARSED_DIR/script_stubs/$SOLUTION" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            script_name=$(xval 'string(/Script/@name)' "$file")
            script_id=$(xval 'string(/Script/@id)' "$file")

            folder_path=$(get_folder_path "$file" "$XML_PARSED_DIR/script_stubs")

            echo "${script_name}|${script_id}|${folder_path}"
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/scripts.index"

    script_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/scripts.index") - 1 ))
    msg "  scripts.index: $script_lines scripts"

    # ---------------------------------------------------------------------------
    # 5. table_occurrences.index
    # ---------------------------------------------------------------------------
    {
        echo "# TOName|TOID|BaseTableName|BaseTableID|Type|DataSource"

        if [[ -d "$XML_PARSED_DIR/table_occurrences/$SOLUTION" ]]; then
        find "$XML_PARSED_DIR/table_occurrences/$SOLUTION" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            to_name=$(xval 'string(/TableOccurrence/@name)' "$file")
            to_id=$(xval 'string(/TableOccurrence/@id)' "$file")
            base_table=$(xval 'string(//BaseTableReference/@name)' "$file")
            base_table_id=$(xval 'string(//BaseTableReference/@id)' "$file")
            to_type=$(xval 'string(/TableOccurrence/@type)' "$file")
            data_source=$(xval 'string(/TableOccurrence/BaseTableSourceReference/DataSourceReference/@name)' "$file")

            echo "${to_name}|${to_id}|${base_table}|${base_table_id}|${to_type}|${data_source}"
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/table_occurrences.index"

    to_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/table_occurrences.index") - 1 ))
    msg "  table_occurrences.index: $to_lines table occurrences"

    # ---------------------------------------------------------------------------
    # 6. value_lists.index
    # ---------------------------------------------------------------------------
    {
        echo "# ValueListName|ValueListID|SourceType|Values"

        if [[ -d "$XML_PARSED_DIR/value_lists/$SOLUTION" ]]; then
        find "$XML_PARSED_DIR/value_lists/$SOLUTION" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            vl_name=$(xval 'string(/ValueList/ValueListReference/@name)' "$file")
            vl_id=$(xval 'string(/ValueList/ValueListReference/@id)' "$file")
            vl_source=$(xval 'string(/ValueList/Source/@value)' "$file")

            # For custom value lists, extract the values (newline-separated in XML)
            vl_values=""
            if [[ "$vl_source" == "Custom" ]]; then
                raw_values=$(xval 'string(/ValueList/CustomValues/Text)' "$file")
                # Replace newlines with commas for single-line format
                vl_values=$(echo "$raw_values" | tr '\n' ',' | sed 's/,$//' | sed 's/^,//')
            elif [[ "$vl_source" == "Field" ]]; then
                vl_values="(field-based)"
            fi

            echo "${vl_name}|${vl_id}|${vl_source}|${vl_values}"
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/value_lists.index"

    vl_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/value_lists.index") - 1 ))
    msg "  value_lists.index: $vl_lines value lists"

    # ---------------------------------------------------------------------------
    # 7. custom_functions.index
    #
    # Classification rules (evaluated in order, first match wins):
    #   utility          – body contains embedded non-FM code (JS/CSS/SVG/HTML)
    #   solution_specific – body contains a TO::Field reference (\w+::\w+)
    #   constant         – zero parameters AND no block-level keywords
    #                      (Let/While/Case/If/For)
    #   functional       – everything else
    # ---------------------------------------------------------------------------
    {
        echo "# FunctionName|FunctionID|Parameters|Access|Display|Category|FolderPath"

        STUB_DIR="$XML_PARSED_DIR/custom_function_stubs/$SOLUTION"
        SANITIZED_DIR="$XML_PARSED_DIR/custom_functions_sanitized/$SOLUTION"

        if [[ -d "$STUB_DIR" ]]; then
        find "$STUB_DIR" -name '*.xml' -type f 2>/dev/null | sort | while IFS= read -r file; do
            cf_name=$(xval 'string(/CustomFunction/@name)' "$file")
            cf_id=$(xval 'string(/CustomFunction/@id)' "$file")
            cf_access=$(xval 'string(/CustomFunction/@access)' "$file")
            cf_display=$(xval 'string(/CustomFunction/Display)' "$file")

            folder_path=$(get_folder_path "$file" "$XML_PARSED_DIR/custom_function_stubs")

            # Extract parameter names from stub
            param_count=$(xval 'string(/CustomFunction/ObjectList/@membercount)' "$file")
            params=""
            if [[ -n "$param_count" && "$param_count" != "0" ]]; then
                for ((p=1; p<=param_count; p++)); do
                    pname=$(xval "string(/CustomFunction/ObjectList/Parameter[$p]/@name)" "$file")
                    if [[ -n "$params" ]]; then
                        params="${params},${pname}"
                    else
                        params="$pname"
                    fi
                done
            fi

            # Classify using the sanitized body — derive the body path from the
            # stub path so nested-in-folder CFs resolve correctly (flat lookup
            # silently misses them and leaves every folder-nested CF mis-
            # classified as "functional").
            category="functional"
            stub_rel="${file#"$STUB_DIR/"}"
            txt_file="$SANITIZED_DIR/${stub_rel%.xml}.txt"

            if [[ -f "$txt_file" ]]; then
                body=$(<"$txt_file")
                body_size=${#body}

                # 1. Utility: embedded JS/CSS/SVG/HTML
                is_utility=0
                if echo "$body" | grep -qE '<svg|<path |<html|<div |<style'; then
                    is_utility=1
                elif [[ "$body_size" -gt 2000 ]] \
                     && echo "$body" | grep -qE 'function[[:space:]]*\(' \
                     && echo "$body" | grep -qEw 'var|const|let|return'; then
                    is_utility=1
                elif echo "$body" | grep -qE '\{margin:|\{padding:|\{display:|\{font-|\{line-height:'; then
                    is_utility=1
                fi

                if [[ "$is_utility" -eq 1 ]]; then
                    category="utility"
                # 2. Solution-specific: TO::Field reference
                elif echo "$body" | grep -qE '[A-Za-z_][A-Za-z0-9_ ]*::[A-Za-z_]'; then
                    category="solution_specific"
                # 3. Constant: zero params + no block keywords
                elif [[ -z "$param_count" || "$param_count" == "0" ]]; then
                    if ! echo "$body" | grep -qiE '\bLet[[:space:]]*\(|\bWhile[[:space:]]*\(|\bCase[[:space:]]*\(|\bIf[[:space:]]*\(|\bFor[[:space:]]*\('; then
                        category="constant"
                    fi
                fi
            fi

            echo "${cf_name}|${cf_id}|${params}|${cf_access}|${cf_display}|${category}|${folder_path}"
        done
        fi
    } > "$SOLUTION_CONTEXT_DIR/custom_functions.index"

    cf_lines=$(( $(wc -l < "$SOLUTION_CONTEXT_DIR/custom_functions.index") - 1 ))
    msg "  custom_functions.index: $cf_lines custom functions"

    solution_total=$(( field_lines + rel_lines + layout_lines + script_lines + to_lines + vl_lines + cf_lines ))
    total_all_lines=$(( total_all_lines + solution_total ))

done

# ---------------------------------------------------------------------------
# Report results
# ---------------------------------------------------------------------------
total_size=$(du -sh "$CONTEXT_DIR" 2>/dev/null | cut -f1)

echo ""
msg "Done!"
msg "  Output: agent/context/ ($total_all_lines total entries, ${total_size})"
msg "  Files per solution: fields.index, relationships.index, layouts.index, scripts.index, table_occurrences.index, value_lists.index, custom_functions.index"
