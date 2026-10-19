/**
 * A field reference with no table and no field reads `<Table Missing>`, the way
 * FileMaker shows it.
 *
 * FileMaker stores a single-field parameter with neither a table nor a field as
 * `<Field table="" id="0" name=""/>` and its Script Workspace line draws
 * `<Table Missing>` in the field slot. The cases below were pasted into
 * FileMaker Pro 26.0.3 and read back: each pair is FileMaker's stored step and
 * the line it displays. XML→HR must print that line, and the line must convert
 * back to the same step, with an empty reference rather than a field called
 * `<Table Missing>`. Parameters the catalog marks `hrNoTableBlank` draw nothing
 * for that reference, and an absent `<Field>` is a different step that draws no
 * field token at all. Mirrors agent/scripts/test_table_missing_hr.py.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { hrToXml, loadCatalog } from '@/converter/hr-to-xml';
import { xmlToHr } from '@/converter/xml-to-hr';
import type { StepCatalogEntry } from '@/converter/catalog-types';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const rawCatalog = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'catalogs', 'step-catalog-en.json'), 'utf-8'),
);
const entries: StepCatalogEntry[] = rawCatalog.steps ?? rawCatalog;
loadCatalog(entries);

const EMPTY = '<Field table="" id="0" name=""/>';

// [step id, step name, FileMaker's stored step body, FileMaker's displayed line],
// measured 2026-10-14.
const CASES: Array<[string, string, string, string]> = [
  ['76', 'Set Field', '<Calculation><![CDATA[1]]></Calculation>' + EMPTY,
    'Set Field [ <Table Missing> ; 1 ]'],
  ['17', 'Go to Field', '<SelectAll state="False"/>' + EMPTY,
    'Go to Field [ <Table Missing> ]'],
  ['61', 'Insert Text', '<SelectAll state="False"/><Text>x</Text>' + EMPTY,
    'Insert Text [ Target: <Table Missing> ; “x” ]'],
  ['116', 'Set Next Serial Value', '<Calculation><![CDATA[1]]></Calculation>' + EMPTY,
    'Set Next Serial Value [ <Table Missing> ; 1 ]'],
  ['77', 'Insert Calculated Result',
    '<SelectAll state="False"/><Calculation><![CDATA[1]]></Calculation>' + EMPTY,
    'Insert Calculated Result [ Target: <Table Missing> ; 1 ]'],
  ['154', 'Sort Records by Field', '<SortRecordsByField value="SortAscending"/>' + EMPTY,
    'Sort Records by Field [ Ascending ; <Table Missing> ]'],
  ['91', 'Replace Field Contents',
    '<NoInteract state="True"/><Restore state="False"/>' + EMPTY +
      '<With value="Calculation"/><Calculation><![CDATA["value"]]></Calculation>' +
      '<SerialNumbers PerformAutoEnter="False" UpdateEntryOptions="False" increment="0"' +
      ' InitialValue="" UseEntryOptions="False"/>',
    'Replace Field Contents [ With dialog: Off ; <Table Missing> ; "value" ]'],
  ['225', 'Save Records as JSONL',
    '<Option state="True"/><CreateDirectories state="False"/><FineTuneFormat state="False"/>' +
      '<AutoOpen state="False"/><CreateEmail state="False"/>' +
      '<UniversalPathList>$path</UniversalPathList>' +
      '<Table id="1065094" name="Invoices"/>' + EMPTY +
      '<SaveAsJSONL>' + EMPTY + '</SaveAsJSONL>',
    'Save Records as JSONL [ Format for fine-tuning: Off ; Table: “Invoices” ; ' +
      'Completion Field: <Table Missing> ; “$path” ; Create folders: Off ]'],
];

const snippet = (id: string, name: string, body: string) =>
  '<fmxmlsnippet type="FMObjectList">' +
  `<Step enable="True" id="${id}" name="${name}">${body}</Step>` +
  '</fmxmlsnippet>';

const fields = (xml: string) =>
  [...new Set((xml.match(/<Field\b[^>]*>/g) ?? []).map((f) => f.replace(/\s*\/>/, '/>')))].sort();

describe('<Table Missing>', () => {
  it.each(CASES)('XML→HR prints FileMaker\'s line (%s %s)', (id, name, body, fmLine) => {
    expect(xmlToHr(snippet(id, name, body))).toBe(fmLine);
  });

  it.each(CASES)('FileMaker\'s line converts back to an empty reference (%s %s)', (id, name, body, fmLine) => {
    const xml = hrToXml(fmLine).xml;
    expect(xml).not.toContain('Table Missing');
    expect(fields(xml)).toEqual(fields(snippet(id, name, body)));
  });

  it('an hrNoTableBlank param draws nothing', () => {
    // Perform Semantic Find as FileMaker stores it unconfigured: the outer Target
    // field draws <Table Missing>, the Save result field nothing.
    const line = xmlToHr(snippet('218', 'Perform Semantic Find',
      EMPTY + '<LLMSemanticFind><Query type="1"/><SaveResult>' + EMPTY +
        '</SaveResult></LLMSemanticFind>'));
    expect(line).toContain('Target field: <Table Missing>');
    expect(line).not.toContain('Save result');
  });

  it('an absent <Field> draws no token', () => {
    expect(xmlToHr(snippet('61', 'Insert Text', '<SelectAll state="False"/><Text>x</Text>')))
      .toBe('Insert Text [ “x” ]');
  });
});
