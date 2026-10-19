/**
 * A field reference with a table but no field reads `Table::<Field Missing>`,
 * the way FileMaker shows it.
 *
 * FileMaker stores a field parameter whose field is not chosen as
 * `<Field table="Invoices" id="0" name=""/>` and its Script Workspace line draws
 * `Invoices::<Field Missing>`. The cases below were pasted into FileMaker Pro
 * 26.0.3 and read back: each pair is FileMaker's stored step and the line it
 * displays. XML→HR must print that line, and the line must convert back to the
 * same step, with an empty field name rather than one called `<Field Missing>`.
 * A reference with no table and no field draws `<Table Missing>` instead
 * (table-missing-hr.test.ts). Mirrors agent/scripts/test_field_missing_hr.py.
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

const MISSING = '<Field table="Invoices" id="0" name=""/>';

// [step id, step name, FileMaker's stored step body, FileMaker's displayed line],
// measured 2026-10-13.
const CASES: Array<[string, string, string, string]> = [
  ['76', 'Set Field', '<Calculation><![CDATA[1]]></Calculation>' + MISSING,
    'Set Field [ Invoices::<Field Missing> ; 1 ]'],
  ['17', 'Go to Field', '<SelectAll state="False"/>' + MISSING,
    'Go to Field [ Invoices::<Field Missing> ]'],
  ['116', 'Set Next Serial Value', '<Calculation><![CDATA[1]]></Calculation>' + MISSING,
    'Set Next Serial Value [ Invoices::<Field Missing> ; 1 ]'],
  ['77', 'Insert Calculated Result',
    '<SelectAll state="False"/><Calculation><![CDATA[1]]></Calculation>' + MISSING,
    'Insert Calculated Result [ Target: Invoices::<Field Missing> ; 1 ]'],
  ['154', 'Sort Records by Field', '<SortRecordsByField value="SortAscending"/>' + MISSING,
    'Sort Records by Field [ Ascending ; Invoices::<Field Missing> ]'],
  ['91', 'Replace Field Contents',
    '<NoInteract state="True"/><Restore state="False"/>' + MISSING +
      '<With value="CurrentContents"/>' +
      '<SerialNumbers PerformAutoEnter="False" UpdateEntryOptions="False" increment="0"' +
      ' InitialValue="" UseEntryOptions="False"/>',
    'Replace Field Contents [ With dialog: Off ; Invoices::<Field Missing> ; Current contents ]'],
  ['225', 'Save Records as JSONL',
    '<Option state="True"/><CreateDirectories state="False"/><FineTuneFormat state="False"/>' +
      '<AutoOpen state="False"/><CreateEmail state="False"/>' +
      '<UniversalPathList>$path</UniversalPathList>' +
      '<Table id="1065094" name="Invoices"/>' + MISSING +
      '<SaveAsJSONL>' + MISSING + '</SaveAsJSONL>',
    'Save Records as JSONL [ Format for fine-tuning: Off ; Table: “Invoices” ; ' +
      'Completion Field: Invoices::<Field Missing> ; “$path” ; Create folders: Off ]'],
];

const snippet = (id: string, name: string, body: string) =>
  '<fmxmlsnippet type="FMObjectList">' +
  `<Step enable="True" id="${id}" name="${name}">${body}</Step>` +
  '</fmxmlsnippet>';

// The distinct <Field> references (FileMaker repeats Save Records as JSONL's
// completion field inside <SaveAsJSONL> and rebuilds it on paste).
const fields = (xml: string) =>
  [...new Set((xml.match(/<Field\b[^>]*>/g) ?? []).map((f) => f.replace(/\s*\/>/, '/>')))].sort();

describe('Table::<Field Missing>', () => {
  it.each(CASES)('XML→HR prints FileMaker\'s line (%s %s)', (id, name, body, fmLine) => {
    expect(xmlToHr(snippet(id, name, body))).toBe(fmLine);
  });

  it.each(CASES)('FileMaker\'s line converts back to an empty field name (%s %s)', (id, name, body, fmLine) => {
    const xml = hrToXml(fmLine).xml;
    expect(xml).not.toContain('Field Missing');
    expect(fields(xml)).toEqual(fields(snippet(id, name, body)));
  });
});
