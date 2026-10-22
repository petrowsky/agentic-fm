/**
 * Regression: a calculation holding "]]>" must be split across CDATA sections
 * (`]]]]><![CDATA[>`). Unsplit, the first "]]>" ends the section early, the
 * XML is malformed and FileMaker refuses the whole paste. FileMaker joins the
 * sections back into one value, as the XML parser does here.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { hrToXml, loadCatalog } from '@/converter/hr-to-xml';
import { xmlToHr } from '@/converter/xml-to-hr';
import type { StepCatalogEntry } from '@/converter/catalog-types';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const CATALOG_PATH = path.join(REPO_ROOT, 'agent', 'catalogs', 'step-catalog-en.json');

const rawCatalog = JSON.parse(readFileSync(CATALOG_PATH, 'utf-8'));
const entries: StepCatalogEntry[] = rawCatalog.steps ?? rawCatalog;
loadCatalog(entries);

const CASES: Array<[string, string[]]> = [
  ['Set Variable [ $a ; Value: "x]]>y" ]', ['"x]]>y"']],
  ['Set Variable [ $b ; Value: "]]>" & "]]>" ]', ['"]]>" & "]]>"']],
  ['Set Variable [ $c ; Value: 1 & "]]>" ]', ['1 & "]]>"']],
  ['Set Variable [ $d ; Value: "]]]>" ]', ['"]]]>"']],
  ['If [ $a = "]]>" ]\nEnd If', ['$a = "]]>"']],
  ['Show Custom Dialog [ "]]>" ; "a]]>b" ]', ['"]]>"', '"a]]>b"']],
];

describe('HR→XML calculation holding "]]>"', () => {
  for (const [hr, calcs] of CASES) {
    it(`splits the CDATA and round-trips: ${hr.split('\n')[0]}`, () => {
      const { xml } = hrToXml(hr);
      expect(xml).toContain(']]]]><![CDATA[>');
      const doc = new DOMParser().parseFromString(xml, 'text/xml');
      expect(doc.getElementsByTagName('parsererror').length).toBe(0);
      const got = Array.from(doc.getElementsByTagName('Calculation')).map((c) => c.textContent);
      for (const calc of calcs) expect(got).toContain(calc);
      expect(xmlToHr(xml).trim()).toBe(hr);
    });
  }
});
