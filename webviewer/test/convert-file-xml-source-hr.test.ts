/**
 * Convert File keeps an XML source and Keep original data.
 *
 * With DataSourceType XMLSource FileMaker keeps the File attributes of
 * `<Profile>`, sets DataType to `"XML "`, adds XMLType / XSLType and one child
 * per source (`<XMLFile>` / `<XMLHttp>` / `<XMLCalc>`, `<XSLFile>` / `<XSLHttp>` /
 * `<XSLCalc>`), and writes an XML calculation a second time as the step-level
 * `<Calculation>`, the catalog's `mirrorOf` param. Every `<ImportOptions>`
 * carries KeepOriginalData. The cases in
 * agent/fixtures/converter/convert-file-xml-source-fm.json were pasted into
 * FileMaker Pro 26.0.3 (one authored in its Specify XML and XSL Options dialog)
 * and read back. Mirrors agent/scripts/test_convert_file_xml_source_hr.py.
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

interface SourceCase {
  id: string;
  pasted: string | null;
  fm: string;
  fmLine: string | null;
  pastedDiffers?: boolean;
}
const CASES: SourceCase[] = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'convert-file-xml-source-fm.json'),
    'utf-8',
  ),
).cases;

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

// Element / attribute structure: whitespace between elements, attribute order,
// character references and the editor-only DisableStepCollapsed ignored.
const canon = (xml: string): string =>
  xml
    .replace(/&#x([0-9a-f]+);/gi, (_m, h: string) => String.fromCharCode(parseInt(h, 16)))
    .replace(/&#(\d+);/g, (_m, d: string) => String.fromCharCode(Number(d)))
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

const roundTrip = (xml: string): string => hrToXml(xmlToHr(snippet(xml))).xml;

describe('Convert File XML source and KeepOriginalData', () => {
  it.each(CASES)("FileMaker's copy-back round-trips ($id)", (c) => {
    expect(canon(roundTrip(c.fm))).toBe(canon(c.fm));
  });

  it.each(CASES.filter((c) => c.pasted && !c.pastedDiffers))(
    "the pasted step converts to FileMaker's copy-back ($id)",
    (c) => {
      expect(canon(roundTrip(c.pasted as string))).toBe(canon(c.fm));
    },
  );

  it.each(CASES.filter((c) => c.fm.includes('<XMLCalc>')))('prints the XML calculation once ($id)', (c) => {
    const calc = /<XMLCalc><Calculation><!\[CDATA\[([\s\S]*?)\]\]>/.exec(c.fm)![1];
    const line = xmlToHr(snippet(c.fm));
    expect(line.split(calc).length - 1).toBe(1);
    expect(line).toContain('XMLCalc=' + calc);
  });
});
