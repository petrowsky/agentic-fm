/**
 * Trigger Claris Connect Flow converts to the step FileMaker stores.
 *
 * FileMaker's line draws only `Flow: <name> ; JSON Data: <calc> ; Target:
 * <target>`; the URL (step-level `<Calculation>`), the AppID:APIKey (`<Auth>`)
 * and the cURL options it writes on insert are stored but never drawn, `<Text>`
 * is its copy of `<Flow>`, and the four option flags are not in its dialog.
 * The catalog prints the stored values as labeled tokens, the cURL options only
 * off FileMaker's default (`hrOmitAtDefault`), and writes `<Text>` from the flow
 * (`mirrorOf: "Flow"`). The cases in
 * agent/fixtures/converter/trigger-claris-connect-flow-fm.json were pasted into
 * FileMaker Pro 26.0.3 and read back. Mirrors
 * agent/scripts/test_trigger_claris_connect_flow_hr.py.
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

interface FlowCase {
  id: string;
  fm: string;
  fmLine: string;
  hr: string;
  roundTrip: 'exact' | 'exceptFlags' | 'exceptCurl' | 'exceptFlagsAndCurl' | 'hrOnly';
  fmLineConverts: boolean;
}
const CASES: FlowCase[] = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'trigger-claris-connect-flow-fm.json'),
    'utf-8',
  ),
).cases;

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

// Element / attribute structure, whitespace and attribute order ignored; field
// ids are not compared (converted without a solution context).
const canon = (xml: string, drop: string[] = []): string => {
  let x = xml.replace(/<DisableStepCollapsed[^>]*>(<\/DisableStepCollapsed>)?/g, '');
  if (drop.includes('flags'))
    x = x.replace(/<(NoInteract|DontEncodeURL|SelectAll|VerifySSLCertificates) state="\w+"\s*\/?>(<\/\1>)?/g, '');
  if (drop.includes('curl')) x = x.replace(/<CURLOptions>[\s\S]*?<\/CURLOptions>/g, '');
  return x
    .replace(/(<Field table="[^"]*") id="\d+"/g, '$1')
    .replace(/<(\w+)([^>]*)><\/\1>/g, '<$1$2/>')
    .replace(/<\?xml[^>]*>|<\/?fmxmlsnippet[^>]*>/g, '')
    .replace(/>\s+</g, '><')
    .trim();
};

const DROP: Record<FlowCase['roundTrip'], string[] | null> = {
  exact: [],
  exceptFlags: ['flags'],
  exceptCurl: ['curl'],
  exceptFlagsAndCurl: ['flags', 'curl'],
  hrOnly: null,
};

describe('Trigger Claris Connect Flow HR', () => {
  it.each(CASES)("prints FileMaker's stored step ($id)", (c) => {
    expect(xmlToHr(snippet(c.fm))).toBe(c.hr);
  });

  it.each(CASES)("the HR converts back to FileMaker's step ($id)", (c) => {
    const drop = DROP[c.roundTrip];
    if (drop === null) return;
    expect(canon(hrToXml(c.hr).xml, drop)).toBe(canon(c.fm, drop));
  });

  it.each(CASES.filter((c) => c.fmLineConverts))("FileMaker's own line converts to its step ($id)", (c) => {
    expect(canon(hrToXml(c.fmLine).xml)).toBe(canon(c.fm));
  });
});
