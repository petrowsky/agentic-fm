/**
 * Replace Field Contents reads and prints `Skip auto-enter options` the way
 * FileMaker draws it.
 *
 * FileMaker draws the bare token `Skip auto-enter options` last on the line
 * exactly when <Restore state="True"/> and <SerialNumbers PerformAutoEnter="False"/>,
 * in every With mode. The catalog models it as a flagStyle projection of the
 * SerialNumbers group's PerformAutoEnter, gated on Restore, so XML -> HR prints
 * the token when FileMaker does and HR -> XML of FileMaker's line writes
 * PerformAutoEnter False and derives Restore True from it.
 *
 * The cases in agent/fixtures/converter/replace-field-contents-fm-lines.json were
 * pasted into FileMaker Pro 26.0.3 and read back. Mirrors
 * agent/scripts/test_replace_field_contents_hr.py.
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

interface FmCase {
  id: string;
  xml: string;
  fmLine: string;
}
const CASES: FmCase[] = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'replace-field-contents-fm-lines.json'),
    'utf-8',
  ),
).cases;

const TOKEN = 'Skip auto-enter options';
const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

const stepOf = (xml: string): Element => {
  const doc = new DOMParser().parseFromString(xml.replace(/^<\?xml[^>]*\?>/, ''), 'text/xml');
  const root = doc.documentElement;
  return root.tagName === 'Step' ? root : (Array.from(root.children).find((c) => c.tagName === 'Step') as Element);
};
const child = (s: Element, el: string): Element | null =>
  Array.from(s.children).find((c) => c.tagName === el) ?? null;

// The stored state FileMaker's line shows: the dialog, the With mode and whether
// auto-enter is skipped (Restore on and PerformAutoEnter off).
function lineState(s: Element): Record<string, string> {
  const sn = child(s, 'SerialNumbers');
  return {
    NoInteract: child(s, 'NoInteract')?.getAttribute('state') ?? '<absent>',
    With: child(s, 'With')?.getAttribute('value') ?? '<absent>',
    skip: String(
      child(s, 'Restore')?.getAttribute('state') === 'True' && sn?.getAttribute('PerformAutoEnter') === 'False',
    ),
  };
}

describe('Replace Field Contents: Skip auto-enter options', () => {
  it('XML -> HR prints FileMaker\'s line', () => {
    for (const c of CASES) expect(xmlToHr(snippet(c.xml)).trim(), c.id).toBe(c.fmLine);
  });

  it('FileMaker\'s line converts back to the state it shows', () => {
    for (const c of CASES) {
      expect(lineState(stepOf(hrToXml(c.fmLine).xml)), `${c.id}: ${c.fmLine}`).toEqual(lineState(stepOf(c.xml)));
    }
  });

  it('the token restores both stored values', () => {
    let s = stepOf(hrToXml(`Replace Field Contents [ With dialog: Off ; Invoices::Description ; Current contents ; ${TOKEN} ]`).xml);
    expect(child(s, 'Restore')?.getAttribute('state')).toBe('True');
    expect(child(s, 'SerialNumbers')?.getAttribute('PerformAutoEnter')).toBe('False');
    expect(child(s, 'With')?.getAttribute('value')).toBe('CurrentContents');
    s = stepOf(hrToXml('Replace Field Contents [ With dialog: Off ; Invoices::Description ; Current contents ]').xml);
    expect(child(s, 'Restore')?.getAttribute('state')).toBe('False');
  });

  it('With None never reads the token as a calculation', () => {
    const s = stepOf(hrToXml(`Replace Field Contents [ With dialog: Off ; Invoices::Description ; ${TOKEN} ]`).xml);
    expect(child(s, 'With')?.getAttribute('value')).toBe('None');
    expect(child(s, 'Calculation')?.textContent ?? '').not.toContain(TOKEN);
  });
});
