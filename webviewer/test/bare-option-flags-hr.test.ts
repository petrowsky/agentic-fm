/**
 * Option flags FileMaker draws bare read back with the state FileMaker stored.
 *
 * FileMaker draws Get Folder Path's Allow Folder Creation, Add Account's and
 * Reset Account Password's Expire password, Go to List of Records' New window,
 * Convert File's Skip Indexes and Show/Hide Toolbars' Include Edit Record Toolbar
 * and Lock as a bare label only when on, and Convert File's Open File only when
 * Option is False. The catalog marks them `flagStyle` (Open File also
 * `invertedHr`), so the label's presence is the value. The cases in
 * agent/fixtures/converter/bare-option-flags-fm-lines.json were pasted into
 * FileMaker Pro 26.0.3 and read back: the stored step and the line FileMaker
 * displays. Mirrors agent/scripts/test_bare_option_flags_hr.py.
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

interface FlagCase {
  step: string;
  flags: Record<string, string>;
  xml: string;
  fmLine: string;
}
const CASES: FlagCase[] = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'bare-option-flags-fm-lines.json'), 'utf-8'),
).cases;

const LABELS: Record<string, string> = {
  AllowFolderCreation: 'Allow Folder Creation',
  ChgPwdOnNextLogin: 'Expire password',
  ShowInNewWindow: 'New window',
  Option: 'Open File',
  SkipIndexes: 'Skip Indexes',
  IncludeEditRecordToolbar: 'Include Edit Record Toolbar',
  Lock: 'Lock',
};

const state = (xml: string, el: string): string | null =>
  new RegExp(`<${el} (?:state|value)="(\\w+)"`).exec(xml)?.[1] ?? null;

const tokens = (line: string): string[] => {
  const m = /\[(.*)\]\s*$/.exec(line);
  return m ? m[1].split(' ; ').map((t) => t.trim()) : [];
};

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

describe('Bare option flag HR', () => {
  it.each(CASES)("FileMaker's line converts back to the stored state ($fmLine)", (c) => {
    const xml = hrToXml(c.fmLine).xml;
    for (const [el, want] of Object.entries(c.flags)) expect(state(xml, el), el).toBe(want);
  });

  it.each(CASES)('XML→HR draws each label when FileMaker does ($fmLine)', (c) => {
    const ours = tokens(xmlToHr(snippet(c.xml)).trim());
    const fm = tokens(c.fmLine);
    for (const el of Object.keys(c.flags)) {
      const label = LABELS[el];
      expect(ours.includes(label), el).toBe(fm.includes(label));
      expect(ours.some((t) => t.startsWith(`${label}:`)), el).toBe(false);
    }
  });

  it.each(CASES)('our line converts back to the stored state ($fmLine)', (c) => {
    const xml = hrToXml(xmlToHr(snippet(c.xml)).trim()).xml;
    for (const [el, want] of Object.entries(c.flags)) expect(state(xml, el), el).toBe(want);
  });
});
