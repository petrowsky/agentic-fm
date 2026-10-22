/**
 * Set Error Logging's custom debug info reads and prints with its label, and is
 * written only when there is one.
 *
 * FileMaker draws the calculation labeled
 * (`Set Error Logging [ On ; Custom debug info: "dbg" ]`, FileMaker Pro 19.6.3
 * to 26.0.3), keeps it while logging is off, and stores no <Calculation> when
 * there is none. The catalog marks the param omitWhenEmpty. The stored bodies
 * below are FileMaker Pro 26.0.3's copy-back of each case (2026-10-20). Mirrors
 * agent/scripts/test_set_error_logging_debug_info_hr.py.
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

const SEMI = 'Get ( ScriptName ) & " ; " & Get ( LastError )';
// [stored state, stored calculation or null, the converter's line]
const CASES: Array<[string, string | null, string]> = [
  ['True', '"dbg"', 'Set Error Logging [ Logging: On ; Custom debug info: "dbg" ]'],
  ['False', '"dbg"', 'Set Error Logging [ Logging: Off ; Custom debug info: "dbg" ]'],
  ['True', SEMI, `Set Error Logging [ Logging: On ; Custom debug info: ${SEMI} ]`],
  ['True', null, 'Set Error Logging [ Logging: On ]'],
  ['False', null, 'Set Error Logging [ Logging: Off ]'],
];

const snippet = (state: string, calc: string | null) =>
  '<fmxmlsnippet type="FMObjectList">' +
  `<Step enable="True" id="200" name="Set Error Logging"><Option state="${state}"/>` +
  (calc === null ? '' : `<Calculation><![CDATA[${calc}]]></Calculation>`) +
  '</Step></fmxmlsnippet>';

describe('Set Error Logging: labeled custom debug info', () => {
  for (const [state, calc, line] of CASES) {
    it(line, () => {
      expect(xmlToHr(snippet(state, calc)).trim()).toBe(line);
      const { xml } = hrToXml(line);
      expect(xml).toContain(`<Option state="${state}"/>`);
      if (calc === null) expect(xml).not.toContain('<Calculation');
      else expect(xml).toContain(`<Calculation><![CDATA[${calc}]]></Calculation>`);
    });
  }
});
