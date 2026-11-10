/**
 * The export and Save Records as steps read and print their option flags as
 * FileMaker draws them.
 *
 * FileMaker draws Automatically open and Create email on Export Field Contents,
 * Export Records and Save Records as Excel / JSONL / PDF / Snapshot Link as bare
 * flags only when on, right after the path (after With dialog on Save Records as
 * PDF); Restore (Excel, PDF) and Append (PDF) are bare flags too. The catalog
 * marks them `flagStyle` and orders the line with `hrSlot`. The cases in
 * agent/fixtures/converter/option-flags-fm-lines.json were pasted into FileMaker
 * Pro 26.0.3 and read back: the stored step and the line FileMaker displays.
 * Mirrors agent/scripts/test_option_flags_hr.py.
 *
 * FileMaker's line is lossy where a step has no path: Export Field Contents and
 * Save Records as JSONL / PDF / Snapshot Link then draw no option flag although
 * the step keeps them on (`hidesOptions`), so that line converts with them off.
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
  xml: string;
  fmLine: string;
  hidesOptions: boolean;
}
const CASES: FlagCase[] = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'option-flags-fm-lines.json'), 'utf-8'),
).cases;

const FLAGS: Record<string, string> = {
  AutoOpen: 'Automatically open',
  CreateEmail: 'Create email',
  Restore: 'Restore',
};
const PDF_FLAGS: Record<string, string> = { ...FLAGS, Option: 'Append' };
const FLAG_LABELS = new Set(Object.values(PDF_FLAGS));

const flagsOf = (step: string): string[] => {
  if (step === 'Save Records as PDF') return Object.keys(PDF_FLAGS);
  if (step === 'Save Records as Excel') return Object.keys(FLAGS);
  return ['AutoOpen', 'CreateEmail'];
};

const state = (xml: string, el: string): string | null =>
  new RegExp(`<${el} state="(\\w+)"`).exec(xml)?.[1] ?? null;

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

// The flag tokens and the quoted path, in line order.
const flagAndPathOrder = (line: string): string[] =>
  line
    .slice(line.indexOf('[') + 1, line.lastIndexOf(']'))
    .split(';')
    .map((t) => t.trim())
    .filter((t) => FLAG_LABELS.has(t) || t.startsWith('“'));

describe('Export / Save Records as option flags HR', () => {
  it.each(CASES)("FileMaker's line converts back with the stored options ($fmLine)", (c) => {
    const xml = hrToXml(c.fmLine).xml;
    for (const el of flagsOf(c.step)) {
      let want = state(c.xml, el);
      if (c.hidesOptions && want !== null && (el === 'AutoOpen' || el === 'CreateEmail')) want = 'False';
      expect(state(xml, el), el).toBe(want);
    }
  });

  it.each(CASES)('XML→HR prints the options as FileMaker draws them ($fmLine)', (c) => {
    const line = xmlToHr(snippet(c.xml));
    for (const label of FLAG_LABELS) expect(line).not.toContain(`${label}:`);
    if (!c.hidesOptions) expect(flagAndPathOrder(line)).toEqual(flagAndPathOrder(c.fmLine));
    const back = hrToXml(line).xml;
    for (const el of flagsOf(c.step)) expect(state(back, el), el).toBe(state(c.xml, el));
  });

  it("prints Save Records as Snapshot Link exactly as FileMaker's line", () => {
    for (const c of CASES) {
      if (c.step === 'Save Records as Snapshot Link' && !c.hidesOptions) expect(xmlToHr(snippet(c.xml))).toBe(c.fmLine);
    }
  });
});

// Save Records as JSONL draws no token for its Option; with it on FileMaker draws
// the source table labeled and quoted after Format for fine-tuning
// (`Table: “Invoices”`) and writes <Table> only then. A Completion Field that
// names no field draws as `<Field Missing>`, which the converters do not print.
const JSONL_CASES = CASES.filter((c) => c.step === 'Save Records as JSONL');
const tableName = (xml: string): string | null => /<Table id="\d*" name="([^"]*)"\/>/.exec(xml)?.[1] ?? null;

describe('Save Records as JSONL Option / Table HR', () => {
  it("prints FileMaker's line", () => {
    for (const c of JSONL_CASES) {
      if (!c.hidesOptions && !c.fmLine.includes('<Field Missing>')) expect(xmlToHr(snippet(c.xml))).toBe(c.fmLine);
    }
  });

  it.each(JSONL_CASES)('keeps the Option, Table and path ($fmLine)', (c) => {
    for (const line of [c.fmLine, xmlToHr(snippet(c.xml))]) {
      const xml = hrToXml(line).xml;
      expect(state(xml, 'Option'), line).toBe(state(c.xml, 'Option'));
      expect(tableName(xml), line).toBe(tableName(c.xml));
      if (c.xml.includes('<UniversalPathList>')) expect(xml, line).toContain('<UniversalPathList>$path</UniversalPathList>');
    }
  });
});

// An option in labeled style that the step does not have is not a path either:
// FileMaker keeps `XMLSource: $xmlPath` as a literal path. A real path, and the
// Profile that holds an XML source, are unaffected. Mirrors test_option_flags_hr.py.
describe('labeled token in a path slot', () => {
  it('is never the path', () => {
    for (const line of [
      'Import Records [ With dialog: Off ; XMLSource: $xmlPath ; XSLT: $xslPath ; Add ; UTF-8 ; Table: Invoices ]',
      'Convert File [ With dialog: Off ; XMLSource: $url ]',
      'Convert File [ With dialog: Off ; XMLSource:$url ]',
      'Convert File [ With dialog: Off ; XMLHttp:https://e.com/a.xml ]',
      'Save Records as PDF [ With dialog: Off ; Output: On ]',
    ]) {
      expect(hrToXml(line).xml).not.toContain('<UniversalPathList');
    }
    expect(hrToXml('Convert File [ With dialog: Off ; XSLT: $x ; "$p" ]').xml).toContain(
      '<UniversalPathList>$p</UniversalPathList>',
    );
  });
  it('leaves real paths and an XML source alone', () => {
    for (const [token, path] of [
      ['$url', '$url'],
      ['"Reports: Q3.csv"', 'Reports: Q3.csv'],
      ['file:data.csv', 'file:data.csv'],
      ['filewin:/C:/data.csv', 'filewin:/C:/data.csv'],
      ['C:/data.csv', 'C:/data.csv'],
      ['https://e.com:8080/a.xml', 'https://e.com:8080/a.xml'],
      ['file:$name', 'file:$name'],
      ['filelinux:$name', 'filelinux:$name'],
    ]) {
      expect(hrToXml(`Convert File [ With dialog: Off ; ${token} ]`).xml).toContain(
        `<UniversalPathList>${path}</UniversalPathList>`,
      );
    }
    expect(
      hrToXml(
        'Convert File [ With dialog: Off ; XMLSource ; Profile: DataType="XML ", XMLType=XMLHttp, XSLType=NoXSL, XMLHttp=https://e.com/a.xml ]',
      ).xml,
    ).toContain('<XMLHttp>https://e.com/a.xml</XMLHttp>');
  });
  it('reports a held-back token, so a dropped path is never silent', () => {
    const held = hrToXml('Convert File [ With dialog: Off ; Reports: Q3.csv ]');
    expect(held.warnings.map((w) => w.message).join()).toContain('"Reports: Q3.csv"');
    expect(hrToXml('Convert File [ With dialog: Off ; "Reports: Q3.csv" ]').warnings).toEqual([]);
  });
});

// A bare flag written in the labeled boolean spelling every other option of the
// step uses (`Append: On`): On sets the flag, Off leaves it clear, and neither,
// nor any other value after the flag's label, is ever read as the file path.
// Before, the unlabeled path took the token positionally and FileMaker drew the
// path `“ On”` (FileMaker Pro 26.0.3). Mirrors test_option_flags_hr.py.
describe('labeled flag spelling', () => {
  it('sets the flag and never becomes the path', () => {
    let xml = hrToXml('Save Records as PDF [ Append: On ; With dialog: Off ]').xml;
    expect(state(xml, 'Option')).toBe('True');
    expect(xml).not.toContain('<UniversalPathList>');
    xml = hrToXml('Export Records [ With dialog: Off ; “$p” ; Automatically open: On ; Create email: Off ]').xml;
    expect(state(xml, 'AutoOpen')).toBe('True');
    expect(state(xml, 'CreateEmail')).toBe('False');
    expect(xml).toContain('<UniversalPathList>$p</UniversalPathList>');
    xml = hrToXml('Export Records [ With dialog: Off ; Automatically open: Yes ]').xml;
    expect(xml).not.toContain('<UniversalPathList>');
  });

  // Sort Records' Restore as FileMaker draws it (`fmLineToken`) next to a sort
  // order is a no-op.
  it("reads Sort Records' drawn Restore as a no-op next to a sort order", () => {
    const withToken = hrToXml('Sort Records [ Restore ; With dialog: Off ; Sort: Invoices::Date=Ascending ]').xml;
    const without = hrToXml('Sort Records [ With dialog: Off ; Sort: Invoices::Date=Ascending ]').xml;
    expect(withToken).toBe(without);
  });
});
