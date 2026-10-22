/**
 * Replace Field Contents writes a <Calculation> only in the Calculation mode.
 *
 * FileMaker stores no <Calculation> for With None, Current contents or Serial
 * numbers. Converting FileMaker's own line for those modes used to write an
 * empty <Calculation><![CDATA[]]></Calculation>, which FileMaker drops on paste.
 * The catalog marks the Calculation param omitWhenEmpty. The lines below are the
 * ones FileMaker Pro 26.0.3 draws for each mode. Mirrors
 * agent/scripts/test_replace_field_contents_calc_omit.py.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { hrToXml, loadCatalog } from '@/converter/hr-to-xml';
import type { StepCatalogEntry } from '@/converter/catalog-types';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const rawCatalog = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'catalogs', 'step-catalog-en.json'), 'utf-8'),
);
const entries: StepCatalogEntry[] = rawCatalog.steps ?? rawCatalog;
loadCatalog(entries);

const CALC = 'Get ( CurrentDate ) & "x"';
const CASES: [string, string][] = [
  ['None', 'Replace Field Contents [ With dialog: Off ; Invoices::Description ]'],
  ['CurrentContents', 'Replace Field Contents [ With dialog: Off ; Invoices::Description ; Current contents ]'],
  ['SerialNumbers', 'Replace Field Contents [ With dialog: Off ; Invoices::Description ; Serial numbers ]'],
  ['Calculation', `Replace Field Contents [ With dialog: Off ; Invoices::Description ; ${CALC} ]`],
];

describe('Replace Field Contents: <Calculation> only in the Calculation mode', () => {
  for (const [withValue, line] of CASES) {
    it(withValue, () => {
      const { xml } = hrToXml(line);
      expect(xml).toContain(`<With value="${withValue}"/>`);
      if (withValue === 'Calculation') {
        expect(xml).toContain(`<Calculation><![CDATA[${CALC}]]></Calculation>`);
      } else {
        expect(xml).not.toContain('<Calculation');
      }
    });
  }
});
