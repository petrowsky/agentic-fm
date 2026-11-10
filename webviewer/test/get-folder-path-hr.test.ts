/**
 * Get Folder Path read from the line FileMaker draws for it.
 *
 * FileMaker draws the target variable first with the repetition as its subscript
 * (`$result[2]`, `$result[$i]`; nothing for 1 or empty), then Dialog title and
 * Default location with no labels, leaving an empty one out:
 * `Get Folder Path [ Allow Folder Creation ; $result ; "t" ; Get ( DocumentsPath ) ]`.
 * The catalog marks Dialog title and Default location `fmLineBare` and the
 * repetition `fmLineSubscriptOf: Name`, so HR -> XML of FileMaker's line keeps
 * every calculation instead of dropping both and writing `$result[2]` as the name.
 *
 * The cases in agent/fixtures/converter/get-folder-path-fm-lines.json were pasted
 * into FileMaker Pro 26.0.3 and read back. FileMaker draws the same line for a
 * lone Dialog title and a lone Default location (`guess`): it reads as Dialog
 * title. Mirrors agent/scripts/test_get_folder_path_hr.py.
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
  kind: 'exact' | 'guess' | 'noVariable';
  xml: string;
  fmLine: string;
}
const CASES: FmCase[] = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'get-folder-path-fm-lines.json'),
    'utf-8',
  ),
).cases;

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

const stepOf = (xml: string): Element => {
  const doc = new DOMParser().parseFromString(xml.replace(/^<\?xml[^>]*\?>/, ''), 'text/xml');
  const root = doc.documentElement;
  return root.tagName === 'Step' ? root : (Array.from(root.children).find((c) => c.tagName === 'Step') as Element);
};
const child = (s: Element, el: string): Element | null =>
  Array.from(s.children).find((c) => c.tagName === el) ?? null;

// Name, repetition and both calculations. `drawn`: as FileMaker's line shows
// them, where repetition 1 and an empty one look the same.
function stored(s: Element, drawn = false): Record<string, string> {
  const calc = (w: string) => (child(s, w)?.getElementsByTagName('Calculation')[0]?.textContent ?? '').trim();
  const rep = calc('Repetition');
  return {
    Name: child(s, 'Name')?.textContent ?? '',
    Repetition: drawn && rep === '1' ? '' : rep,
    DialogTitle: calc('DialogTitle'),
    DefaultLocation: calc('DefaultLocation'),
  };
}

describe('Get Folder Path: FileMaker\'s line', () => {
  it('keeps every calculation', () => {
    for (const c of CASES.filter((x) => x.kind === 'exact')) {
      expect(stored(stepOf(hrToXml(c.fmLine).xml)), `${c.id}: ${c.fmLine}`).toEqual(stored(stepOf(c.xml), true));
    }
  });

  it('reads a lone unlabeled calculation as the dialog title', () => {
    for (const c of CASES.filter((x) => x.kind === 'guess')) {
      const want = stored(stepOf(c.xml), true);
      want.DialogTitle = want.DialogTitle || want.DefaultLocation;
      want.DefaultLocation = '';
      expect(stored(stepOf(hrToXml(c.fmLine).xml)), `${c.id}: ${c.fmLine}`).toEqual(want);
    }
  });

  it('places the default location from an empty slot or its label', () => {
    for (const hr of [
      'Get Folder Path [ Allow Folder Creation ; $result[2] ; ; Get ( DocumentsPath ) ]',
      'Get Folder Path [ Allow Folder Creation ; $result[2] ; Default location: Get ( DocumentsPath ) ]',
    ]) {
      expect(stored(stepOf(hrToXml(hr).xml)), hr).toEqual({
        Name: '$result',
        Repetition: '2',
        DialogTitle: '',
        DefaultLocation: 'Get ( DocumentsPath )',
      });
    }
  });

  it('round-trips every step through the converter\'s HR', () => {
    for (const c of CASES) {
      const line = xmlToHr(snippet(c.xml)).trim();
      expect(stored(stepOf(hrToXml(line).xml)), `${c.id}: ${line}`).toEqual(stored(stepOf(c.xml)));
    }
  });
});
