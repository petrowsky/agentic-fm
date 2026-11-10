/**
 * Five steps read from the bare `On` / `Off` FileMaker draws for them.
 *
 * FileMaker draws the on/off option of Set Error Logging, Set AI Call Logging,
 * Set Layout Object Animation, Set Revert Transaction on Error and Set Use System
 * Formats with no label (`Set Layout Object Animation [ Off ]`). The converter's
 * HR labels it (`Animation: Off`), so FileMaker's own line used to read back with
 * the step's default state. The catalog marks each of these booleans
 * `fmLineBare`: HR -> XML takes a bare token naming one of its values.
 *
 * The cases in agent/fixtures/converter/on-off-fm-lines.json were pasted into
 * FileMaker Pro 26.0.3 and read back. Mirrors
 * agent/scripts/test_on_off_lines_hr.py.
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
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'on-off-fm-lines.json'), 'utf-8'),
).cases;

// The on/off state, the calculation and which logging flags are present.
function stored(xml: string): string {
  let out = /state="(\w+)"/.exec(xml)?.[1] ?? '?';
  const c = /<!\[CDATA\[([\s\S]*?)\]\]>/.exec(xml);
  if (c && c[1]) out += '|calc=' + c[1];
  if (xml.includes('<VerboseMode')) out += '|verbose';
  if (xml.includes('<TruncateEmbeddingVectorsMode')) out += '|truncate';
  return out;
}

describe('bare On / Off on FileMaker\'s line', () => {
  it('keeps the state', () => {
    for (const c of CASES) expect(stored(hrToXml(c.fmLine).xml), `${c.id}: ${c.fmLine}`).toEqual(stored(c.xml));
  });

  it('still reads the labeled spelling', () => {
    expect(stored(hrToXml('Set Layout Object Animation [ Animation: Off ]').xml)).toEqual('False');
    expect(stored(hrToXml('Set Revert Transaction on Error [ Revert on error: On ]').xml)).toEqual('True');
    expect(stored(hrToXml('Set Error Logging [ Logging: On ; Custom debug info: "dbg" ]').xml)).toEqual('True|calc="dbg"');
  });

  it('round-trips every step through the converter\'s HR', () => {
    for (const c of CASES) {
      const line = xmlToHr(`<fmxmlsnippet type="FMObjectList">${c.xml}</fmxmlsnippet>`).trim();
      expect(stored(hrToXml(line).xml), `${c.id}: ${line}`).toEqual(stored(c.xml));
    }
  });
});
