/**
 * An option group at its defaults prints nothing.
 *
 * FileMaker never draws Speak's SpeechOptions or Create PDF's Document /
 * Security / View on its line: `Speak [ "hi" ]` and `Create PDF [ ]` whatever
 * they hold. The catalog marks those groups `hrOmitAtDefault`: XML→HR prints no
 * token while the element equals its catalog default, and HR→XML writes that
 * default element back when the token is absent, so nothing is lost either way.
 * A group off its default still prints its whole body. The cases in
 * agent/fixtures/converter/attrgroup-defaults-fm-lines.json were pasted into
 * FileMaker Pro 26.0.3 and read back: the stored step and the line FileMaker
 * displays. Mirrors agent/scripts/test_attrgroup_defaults_hr.py.
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

interface DefaultsCase {
  id: string;
  xml: string;
  fmLine: string;
}
const CASES: DefaultsCase[] = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'attrgroup-defaults-fm-lines.json'),
    'utf-8',
  ),
).cases;

// Cases whose options sit at the catalog defaults: our line is FileMaker's.
const AT_DEFAULT = new Set(['S1', 'S2', 'S6', 'C0', 'C0R']);
const GROUP_LABELS = ['Speech options:', 'Document:', 'Security:', 'View:'];

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

// Element / attribute structure, whitespace and attribute order ignored, and the
// empty required calculation FileMaker drops on paste (Speak with no text).
const canon = (xml: string): string =>
  xml
    .replace(/<DisableStepCollapsed[^>]*>(<\/DisableStepCollapsed>)?/g, '')
    .replace(/<Calculation><!\[CDATA\[\]\]><\/Calculation>/g, '')
    .replace(/<(\w+)([^>]*)><\/\1>/g, '<$1$2/>')
    .replace(/<(\w+)((?:\s+\w+="[^"]*")*)\s*(\/?>)/g, (_m, tag: string, attrs: string, end: string) => {
      const pairs = [...attrs.matchAll(/(\w+)="([^"]*)"/g)].map((a) => [a[1], a[2]]).sort((a, b) =>
        a[0] < b[0] ? -1 : a[0] > b[0] ? 1 : 0);
      return '<' + tag + pairs.map(([k, v]) => ` ${k}="${v}"`).join('') + end;
    })
    .replace(/<\?xml[^>]*>|<\/?fmxmlsnippet[^>]*>/g, '')
    .replace(/>\s+</g, '><')
    .trim();

describe('attrGroup at its defaults (hrOmitAtDefault)', () => {
  it.each(CASES)('prints the group only off its defaults ($id: $fmLine)', (c) => {
    const line = xmlToHr(snippet(c.xml));
    if (AT_DEFAULT.has(c.id)) {
      for (const label of GROUP_LABELS) expect(line).not.toContain(label);
      if (!c.fmLine.endsWith(' [ ]')) expect(line).toBe(c.fmLine);
    } else {
      expect(GROUP_LABELS.some((label) => line.includes(label))).toBe(true);
    }
  });

  it.each(CASES)("our line converts back to FileMaker's step ($id)", (c) => {
    expect(canon(hrToXml(xmlToHr(snippet(c.xml))).xml)).toBe(canon(c.xml));
  });

  it('writes the default options back when no token names them', () => {
    expect(hrToXml('Speak [ "hi" ]').xml).toContain('<SpeechOptions WaitForCompletion="False" VoiceId="0"/>');
    const c0r = CASES.find((c) => c.id === 'C0R')!;
    expect(canon(hrToXml('Create PDF [ Restore ]').xml)).toBe(canon(c0r.xml));
  });
});
