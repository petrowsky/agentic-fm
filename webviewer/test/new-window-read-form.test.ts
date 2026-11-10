/**
 * New Window read from the line FileMaker draws for it, and its layout choice.
 *
 * FileMaker draws every layout choice after `Using layout:` (a named layout as
 * `“Name” (TO)`), and the catalog prints that label. Its value-less
 * `hrLabelWhen` variant still reads the older `Layout:`, the layout token drops
 * its curly quotes and table occurrence, and the step is the one
 * `Layout: "Name"` writes. The layout is discriminated by LayoutDestination, so
 * the current layout and both by-calculation forms survive XML -> HR -> XML.
 * Cases are FileMaker Pro 26.0.3 copies (agent/fixtures/converter/
 * new-window-layout-fm.json). Mirrors agent/scripts/test_new_window_layout_hr.py.
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

interface Case { id: string; fmLine: string; fm: string }
const cases: Case[] = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'new-window-layout-fm.json'), 'utf-8'),
).cases.filter((c: Case) => c.fm.includes('"New Window"'));

/** [LayoutDestination, the layout it names], ids and empty-layout spellings normalised. */
function layoutOf(xml: string): [string, string] {
  const dest = /<LayoutDestination value="([^"]*)"/.exec(xml)?.[1] ?? '';
  const calc = /<Layout[^>]*>\s*<Calculation>([\s\S]*?)<\/Calculation>/.exec(xml);
  if (calc) return [dest, 'calc:' + calc[1].trim().replace(/^<!\[CDATA\[([\s\S]*)\]\]>$/, '$1')];
  if (dest === 'CurrentLayout') return [dest, ''];
  return [dest, /<Layout\b[^>]*\bname="([^"]*)"/.exec(xml)?.[1] ?? ''];
}

describe('New Window read form', () => {
  it('takes the line FileMaker draws', () => {
    const written = hrToXml('New Window [ Style: Document ; Layout: "Clients" ]').xml;
    expect(written).toContain('name="Clients"');
    expect(hrToXml('New Window [ Style: Document ; Using layout: “Clients” (Primary) ]').xml).toBe(written);
  });

  it('prints Using layout and keeps the layout choice through HR', () => {
    for (const c of cases) {
      const hr = xmlToHr('<fmxmlsnippet type="FMObjectList">' + c.fm + '</fmxmlsnippet>');
      expect(hr, c.id).toContain('Using layout: ');
      expect(hr, c.id).not.toContain('; Layout: ');
      let want = layoutOf(c.fm);
      // A quoted literal name calculation prints as a named layout (the same layout opens).
      if (c.id === 'NAMECALC-LIT') want = ['SelectedLayout', 'Dashboard'];
      expect(layoutOf(hrToXml(hr.trim()).xml), c.id).toEqual(want);
    }
  });
});
