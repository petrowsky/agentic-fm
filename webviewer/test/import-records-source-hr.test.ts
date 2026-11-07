/**
 * Import Records keeps its source Profile.
 *
 * Import Records holds its source format in the same `<Profile>` as Convert
 * File: a file source's FieldDelimiter / IsPredefined / FieldNameRow / DataType
 * (`table` on a FileMaker source), a folder source's defaults, and an XML
 * source's XMLType / XSLType with one child per source, plus the step-level
 * `<Calculation>` copy of an XML calculation (the catalog's `mirrorOf` param).
 * `<ImportOptions>` carries KeepOriginalData and, when set, AddRemainingRecords.
 * The cases in agent/fixtures/converter/import-records-source-fm.json were
 * pasted into FileMaker Pro 26.0.3 and read back. FileMaker fills
 * `<TargetFields>` with every field of the target table, which the converter
 * cannot resolve without the file, so the comparison leaves the field mapping
 * and the table id out. Mirrors agent/scripts/test_import_records_source_hr.py.
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
  fm: string;
  restoreFromCompanions?: boolean;
}
const FIXTURE = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'import-records-source-fm.json'),
    'utf-8',
  ),
);
const ALL: SourceCase[] = FIXTURE.cases;
// Cases marked restoreFromCompanions hold a Restore state the converter derives
// from the import options and table, not from the source.
const CASES = ALL.filter((c) => !c.restoreFromCompanions);

const snippet = (stepXml: string) => `<fmxmlsnippet type="FMObjectList">${stepXml}</fmxmlsnippet>`;

// Element / attribute structure: whitespace between elements, attribute order,
// character references and the editor-only DisableStepCollapsed ignored. A line
// break reads as a line feed, as an XML parser reads FileMaker's carriage return.
const canon = (xml: string): string =>
  xml
    .replace(/\r\n?/g, '\n')
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

// The step without its field mapping and with no table id.
const source = (xml: string): string =>
  canon(
    xml
      .replace(/<TargetFields>[\s\S]*<\/TargetFields>|<TargetFields\s*\/>/g, '')
      .replace(/(<Table\b[^>]*?\bid=")[^"]*(")/g, '$1$2'),
  );

const roundTrip = (xml: string): string => hrToXml(xmlToHr(snippet(xml))).xml;

describe('Import Records source Profile', () => {
  it('holds every case', () => {
    expect(ALL).toHaveLength(25);
    expect(CASES).toHaveLength(22);
  });

  it.each(CASES)("FileMaker's copy-back round-trips ($id)", (c) => {
    expect(source(roundTrip(c.fm))).toBe(source(c.fm));
  });

  it.each(ALL.filter((c) => c.fm.includes('XMLType=')))('prints the XML source in the Profile ($id)', (c) => {
    const line = xmlToHr(snippet(c.fm));
    expect(line).toContain('Profile: ');
    expect(line).toContain('XMLType=');
  });

  it.each(ALL.filter((c) => c.fm.includes('<XMLCalc>')))('prints the XML calculation once ($id)', (c) => {
    const calc = /<XMLCalc><Calculation><!\[CDATA\[([\s\S]*?)\]\]>/.exec(c.fm)![1];
    const line = xmlToHr(snippet(c.fm));
    expect(line.split(calc).length - 1).toBe(1);
    expect(line).toContain('XMLCalc=' + calc);
  });

  // FileMaker draws these with the stored import order (FM 26.0.3): the action
  // it writes as Update draws Replace, the one it writes as UpdateOnMatch draws
  // Update.
  it.each([
    ['Add', 'Add', 'UTF-8', 'UTF-8'],
    ['Update', 'UpdateOnMatch', 'Windows ANSI', 'Windows'],
    ['Replace', 'Update', 'Mac Roman', 'Macintosh'],
    ['Update', 'UpdateOnMatch', 'DOS OEM', 'DOS'],
    ['Replace', 'Update', 'Korean (EUC-KR)', 'EUC-KR'],
  ])('reads and prints the labels %s (%s) and %s (%s)', (label, method, shown, stored) => {
    const xml = hrToXml(`Import Records [ With dialog: On ; Table: T ; "$p" ; ${label} ; ${shown} ]`).xml;
    expect(xml).toContain(`method="${method}"`);
    expect(xml).toContain(`CharacterSet="${stored}"`);
    expect(xmlToHr(xml)).toMatch(new RegExp(`; ${label} ; ${shown.replace(/[()]/g, '\\$&')}( ;| \\]$)`));
  });

  it('writes the default options for a table line with no method or character set', () => {
    // Given no <ImportOptions>, FileMaker reads the file as Mac Roman. Written
    // back at its catalog defaults (FM 26.0.3), the step keeps UTF-8 and draws
    // `Add; UTF-8`. Off the defaults, the tokens still win.
    const xml = hrToXml('Import Records [ With dialog: Off ; Table: Invoices ; "$n" ]').xml;
    expect(xml).toContain('<Restore state="True"');
    expect(xml).toContain('CharacterSet="UTF-8"');
    expect(xml).toContain('method="Add"');
    expect(xml).toContain('AutoEnter="True"');
    expect(xml).not.toContain('AddRemainingRecords');
    const set = hrToXml('Import Records [ With dialog: Off ; Table: Invoices ; "$n" ; Update ; Mac Roman ]').xml;
    expect(set).toContain('CharacterSet="Macintosh"');
    expect(set).toContain('method="UpdateOnMatch"');
  });
});
