/**
 * Copy, Cut, Clear, Check Selection, Paste and Execute FileMaker Data API read
 * and print their Select flag as FileMaker draws it.
 *
 * FileMaker draws Select (and Paste's No style) as a bare flag only when on:
 * `Copy [ Select ; Invoices::Description ]`, `Copy [ Invoices::Description ]`.
 * The catalog marks these `flagStyle`, so the bare label reads back as on and its
 * absence as off; the same rule covers Go to Field's Select/perform and the
 * Insert steps' Select, whose default is on. Paste's Link if available is never
 * drawn by FileMaker, so its line converts with it off; our own line prints it
 * bare when on. The cases in agent/fixtures/converter/select-flags-fm-lines.json
 * were pasted into FileMaker Pro 26.0.3 and read back: the stored step and the
 * line FileMaker displays. Mirrors agent/scripts/test_select_flags_hr.py.
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

interface FlagCase {
  step: string;
  xml: string;
  fmLine: string;
}
const CASES: FlagCase[] = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'select-flags-fm-lines.json'), 'utf-8'),
).cases;

const FLAGS = ['SelectAll', 'NoStyle', 'LinkAvail'];
const LINK = 'Link if available ; ';

const state = (xml: string, el: string): string | null =>
  new RegExp(`<${el} state="(\\w+)"`).exec(xml)?.[1] ?? null;

// The field reference, or null for none at all. A step stored with no <Field>
// (`Copy [ Select ]`) emits an empty one; that absent-field round trip is a
// separate gap, so the cases compare the field only where FileMaker stored one.
const field = (xml: string): string | null => {
  const m = /<Field table="([^"]*)" id="\d+" name="([^"]*)"/.exec(xml);
  return m ? `${m[1]}::${m[2]}` : null;
};

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

describe('Select flag HR', () => {
  it.each(CASES)("FileMaker's line converts back to the stored step ($fmLine)", (c) => {
    const xml = hrToXml(c.fmLine).xml;
    for (const el of FLAGS) {
      let want = state(c.xml, el);
      if (el === 'LinkAvail' && want !== null) want = 'False'; // FileMaker's line does not carry it
      expect(state(xml, el), el).toBe(want);
    }
    if (field(c.xml)) expect(field(xml)).toBe(field(c.xml));
  });

  it.each(CASES)("XML→HR prints FileMaker's line ($fmLine)", (c) => {
    const line = xmlToHr(snippet(c.xml)).trim();
    expect(line.includes(LINK)).toBe(state(c.xml, 'LinkAvail') === 'True');
    expect(line.replace(LINK, '')).toBe(c.fmLine);
  });

  it.each(CASES)('our line converts back to the stored step ($fmLine)', (c) => {
    const line = xmlToHr(snippet(c.xml)).trim();
    const xml = hrToXml(line).xml;
    for (const el of FLAGS) expect(state(xml, el), el).toBe(state(c.xml, el));
    if (field(c.xml)) expect(field(xml)).toBe(field(c.xml));
  });
});
