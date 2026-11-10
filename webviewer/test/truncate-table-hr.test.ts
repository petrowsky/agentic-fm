/**
 * Truncate Table's table name drawn the way FileMaker draws it.
 *
 * FileMaker Pro 26.0.3 draws a named table quoted, `Table: “Line Items”`, and the
 * current table bare, `Table: <Current Table>`. It never escapes the name it
 * displays, so a name holding a curly quote shows as `“QFC“Probe”`; XML→HR writes
 * that one as an escaped literal (`“QFC\“Probe”`) so it reads back unambiguously.
 * The (stored step, FileMaker's displayed line) pairs below were pasted into
 * FileMaker and read back. XML→HR must print FileMaker's line (or its escaped
 * form), and both lines must convert back to the same table. Mirrors
 * agent/scripts/test_truncate_table_hr.py.
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

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// [stored id, stored name, FileMaker's displayed line, our line], measured 2026-10-30.
const CASES: Array<[string, string, string, string]> = [
  ['143', 'Testing', 'Truncate Table [ With dialog: Off ; Table: “Testing” ]', ''],
  ['138', 'Line Items', 'Truncate Table [ With dialog: Off ; Table: “Line Items” ]', ''],
  ['301', 'QFC¶Pil', 'Truncate Table [ With dialog: Off ; Table: “QFC¶Pil” ]', ''],
  ['300', 'QFC“Probe', 'Truncate Table [ With dialog: Off ; Table: “QFC“Probe” ]',
    'Truncate Table [ With dialog: Off ; Table: “QFC\\“Probe” ]'],
  ['-1', '<Current Table>', 'Truncate Table [ With dialog: Off ; Table: <Current Table> ]', ''],
];

const snippet = (id: string, name: string) =>
  '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="182" name="Truncate Table">' +
  `<NoInteract state="True"/><BaseTable id="${id}" comment="" name="${esc(name)}"/></Step></fmxmlsnippet>`;

const baseTable = (id: string, name: string) =>
  id === '-1'
    ? `<BaseTable id="-1" name="${esc(name)}" comment=""/>`
    : `<BaseTable name="${esc(name)}" comment=""/>`;

describe('Truncate Table table name', () => {
  it.each(CASES)('XML→HR prints FileMaker\'s line (%s %s)', (id, name, fm, ours) => {
    expect(xmlToHr(snippet(id, name))).toBe(ours || fm);
  });

  it.each(CASES)('both lines convert back to the table (%s %s)', (id, name, fm, ours) => {
    for (const line of [fm, ours || fm]) {
      expect(hrToXml(line).xml).toContain(baseTable(id, name));
    }
  });
});
