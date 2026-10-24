/**
 * Create PDF's step-level calculation is the Title's copy.
 *
 * FileMaker writes the Document Title of Create PDF and Save Records as PDF a
 * second time, as the step-level `<Calculation>`: pasted with only the Title it
 * adds the copy, pasted with only the copy it adds the Title, and with both set
 * to different values it keeps the Title in both. The catalog marks that param
 * `hrHidden` + `mirrorOf`: XML→HR prints the value once, as the Title (read from
 * the copy when the Title is absent), and HR→XML writes both from it. The cases
 * in agent/fixtures/converter/pdf-title-copy-fm.json were pasted into FileMaker
 * Pro 26.0.3 and read back. Mirrors agent/scripts/test_pdf_title_copy_hr.py.
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

interface TitleCase {
  id: string;
  pasted: string;
  fm: string;
  fmLine: string;
  titleFacetOnly?: boolean;
}
const CASES: TitleCase[] = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'pdf-title-copy-fm.json'), 'utf-8'),
).cases;

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

// Element / attribute structure, whitespace and attribute order ignored.
const canon = (xml: string): string =>
  xml
    .replace(/<DisableStepCollapsed[^>]*>(<\/DisableStepCollapsed>)?/g, '')
    .replace(/<(\w+)([^>]*)><\/\1>/g, '<$1$2/>')
    .replace(/<(\w+)((?:\s+\w+="[^"]*")*)\s*(\/?>)/g, (_m, tag: string, attrs: string, end: string) => {
      const pairs = [...attrs.matchAll(/(\w+)="([^"]*)"/g)].map((a) => [a[1], a[2]]).sort((a, b) =>
        a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
      return '<' + tag + pairs.map(([k, v]) => ` ${k}="${v}"`).join('') + end;
    })
    .replace(/<\?xml[^>]*>|<\/?fmxmlsnippet[^>]*>/g, '')
    .replace(/>\s+</g, '><')
    .trim();

const CALC = /<Calculation><!\[CDATA\[([\s\S]*?)\]\]><\/Calculation>/g;
// The step-level calculation(s) and the Title(s) a step holds.
const titleFacet = (xml: string): [string[], string[]] => {
  const top = xml.replace(/<(CreatePDFFile|PDFOptions)\b[\s\S]*?<\/\1>/g, '');
  const titles = [...xml.matchAll(/<Title>\s*<Calculation><!\[CDATA\[([\s\S]*?)\]\]><\/Calculation>/g)];
  return [[...top.matchAll(CALC)].map((m) => m[1]), titles.map((m) => m[1])];
};

const convertsToFileMakersStep = (c: TitleCase, xml: string): void => {
  const out = hrToXml(xmlToHr(snippet(xml))).xml;
  if (c.titleFacetOnly) expect(titleFacet(out)).toEqual(titleFacet(c.fm));
  else expect(canon(out)).toBe(canon(c.fm));
};

describe('PDF step-level calculation is the Title copy (mirrorOf)', () => {
  it.each(CASES)('prints the value once, as the Title ($id)', (c) => {
    const [, titles] = titleFacet(c.fm);
    for (const xml of [c.pasted, c.fm]) {
      const line = xmlToHr(snippet(xml));
      expect(line).not.toContain('Output File');
      expect(line).not.toContain('Saved label');
      if (titles.length) expect(line.split(titles[0]).length - 1).toBe(1);
    }
  });

  it.each(CASES)("the pasted step converts to FileMaker's copy-back ($id)", (c) => {
    convertsToFileMakersStep(c, c.pasted);
  });

  it.each(CASES)("FileMaker's copy-back round-trips ($id)", (c) => {
    convertsToFileMakersStep(c, c.fm);
  });
});
