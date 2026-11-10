/**
 * Import Records keeps each field's import options and, with Restore off, its
 * stored import order.
 *
 * FileMaker keeps a FieldOptions number on every mapped `<Field>` and never
 * draws it; the HR writes it after the map, `Invoices::Date=Import:2`, when it
 * is not 0 (the catalog's `hrEntrySuffix`). The cases in
 * agent/fixtures/converter/import-records-field-options-fm.json were pasted into
 * FileMaker Pro 26.0.3 and copied back; XML -> HR -> XML of FileMaker's copy
 * keeps the mapping (field ids and tables aside: unresolved without the file).
 * Mirrors agent/scripts/test_import_records_field_options_hr.py.
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

interface OptionsCase {
  id: string;
  fm: string;
}
const FIXTURE = JSON.parse(
  readFileSync(
    path.join(REPO_ROOT, 'agent', 'fixtures', 'converter', 'import-records-field-options-fm.json'),
    'utf-8',
  ),
);
const ALL: OptionsCase[] = FIXTURE.cases;
// ROFF-BARE holds no import order: written back, the step carries the default
// import options and an empty table, which FileMaker keeps (the converter writes
// them for every Import Records).
const CASES = ALL.filter((c) => c.id !== 'ROFF-BARE');

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

// The step with no table id and no field id or table: unresolved without the file.
const mapping = (xml: string): string =>
  canon(
    xml
      .replace(/(<Table\b[^>]*?\bid=")[^"]*(")/g, '$1$2')
      .replace(/(<Field\b[^>]*?) (?:id|table)="[^"]*"/g, '$1')
      .replace(/(<Field\b[^>]*?) (?:id|table)="[^"]*"/g, '$1'),
  );

const roundTrip = (xml: string): string => hrToXml(xmlToHr(snippet(xml))).xml;

describe('Import Records field options and stored order', () => {
  it('holds every case', () => {
    expect(ALL).toHaveLength(15);
    expect(CASES).toHaveLength(14);
  });

  it.each(CASES)("FileMaker's copy-back round-trips ($id)", (c) => {
    expect(mapping(roundTrip(c.fm))).toBe(mapping(c.fm));
  });

  it('keeps the stored order with Restore off', () => {
    // FileMaker keeps the table, options and mapping with Restore off and draws
    // none of them; the HR shows what is not the default and says the order is off.
    const line = (id: string) => xmlToHr(snippet(ALL.find((c) => c.id === id)!.fm));
    const full = line('ROFF-FULL');
    expect(full.startsWith('Import Records [ With dialog: Off ; Specify import order: Off ; Table: Invoices ;')).toBe(true);
    expect(full).toContain('; Update ; Windows ANSI ;');
    expect(full).toContain('AutoEnter=False');
    expect(full).toContain('Name=Match, Status=Import:2, Date=DoNotImport:1');
    expect(line('ROFF-STD')).toContain('Specify import order: Off ; Table: Invoices');
    expect(line('ROFF-STD')).not.toContain('; Add ;');
    expect(line('ROFF-BARE')).not.toContain('Specify import order');
    expect(line('RON-FULL')).not.toContain('Specify import order');
    const off = hrToXml('Import Records [ Specify import order: Off ; Table: T ; Import fields: T::A ]').xml;
    expect(off).toContain('<Restore state="False"');
    expect(off).toContain('name="T"');
    expect(hrToXml('Import Records [ Specify import order: On ]').xml).toContain('<Restore state="True"');
  });

  it('prints the field options after the map', () => {
    const line = xmlToHr(snippet(ALL.find((c) => c.id === 'FO-3')!.fm));
    expect(line).toContain('Name=Import:1, Date=Import:3, PrimaryKey=DoNotImport:2');
    expect(line).toContain('ForeignKeyClient=DoNotImport,');
    const xml = hrToXml('Import Records [ Table: T ; Import fields: T::A=Import:2, T::B=:16, T::C=Match ]').xml;
    expect(xml).toContain('FieldOptions="2" map="Import"');
    expect(xml).toContain('FieldOptions="16" map="Import"');
    expect(xml).toContain('FieldOptions="0" map="Match"');
  });
});
