/**
 * Show/Hide Toolbars and Convert File print their options in the order
 * FileMaker draws them.
 *
 * FileMaker draws `Show/Hide Toolbars [ Lock ; Include Edit Record Toolbar ;
 * Hide ]` and `Convert File [ Open File ; Skip Indexes ; With dialog: On ;
 * “$path” ; Verify SSL Certificates ]`. It never draws Convert File's source
 * type, profile or import options. The catalog orders both steps' HR with
 * `hrSlot` and marks those three params `fmLineHidden`. The cases in
 * agent/fixtures/converter/bare-flag-order-fm-lines.json were pasted into
 * FileMaker Pro 26.0.3 and read back: the stored step and the line FileMaker
 * displays. Mirrors agent/scripts/test_bare_flag_order_hr.py.
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

interface OrderCase {
  id: string;
  xml: string;
  fmLine: string;
}
const CASES: OrderCase[] = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'bare-flag-order-fm-lines.json'), 'utf-8'),
).cases;

const OPTIONS = [
  'IncludeEditRecordToolbar', 'Lock', 'ShowHide',
  'NoInteract', 'Option', 'SkipIndexes', 'VerifySSLCertificates',
];
const PATH = '<UniversalPathList>$path</UniversalPathList>';

const state = (xml: string, el: string): string | null =>
  new RegExp(`<${el} (?:state|value)="(\\w+)"`).exec(xml)?.[1] ?? null;

const tokens = (line: string): string[] => {
  const m = /\[ (.*) \]\s*$/.exec(line);
  return m ? m[1].split(' ; ') : [];
};

// The tokens of a line FileMaker would draw: no source type, profile or import options.
const shown = (line: string): string[] =>
  tokens(line).filter(
    (t) => t !== 'File' && t !== 'XMLSource' && !t.startsWith('Profile: ') && !t.startsWith('Import options: '),
  );

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

describe('Option order on FileMaker\'s line', () => {
  it.each(CASES)('XML→HR prints FileMaker\'s order ($id)', (c) => {
    const line = xmlToHr(snippet(c.xml)).trim();
    expect(shown(line)).toEqual(tokens(c.fmLine));
    if (c.fmLine.startsWith('Show/Hide Toolbars')) expect(line).toBe(c.fmLine);
  });

  it.each(CASES)("FileMaker's line converts back to the stored state ($id)", (c) => {
    const xml = hrToXml(c.fmLine).xml;
    for (const el of OPTIONS) {
      const want = state(c.xml, el);
      if (want !== null) expect(state(xml, el), el).toBe(want);
    }
    expect(xml.includes(PATH)).toBe(c.xml.includes(PATH));
  });

  it.each(CASES)('our line round-trips ($id)', (c) => {
    const line = xmlToHr(snippet(c.xml)).trim();
    expect(xmlToHr(hrToXml(line).xml).trim()).toBe(line);
  });
});
