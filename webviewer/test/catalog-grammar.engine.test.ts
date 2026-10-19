/**
 * Engine-level regression net for catalog-grammar.ts, mirroring the Python
 * agent/scripts/test_catalog_grammar_engine.py so the two ports stay structurally
 * parallel (a facet regressed in one is obviously flagged in the other). These
 * lock the port's *behaviour* (that it faithfully applies specific catalog grammar
 * facets); the 216-step byte-identity suite is the comprehensive gate.
 */
import { describe, it, expect, beforeAll } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import {
  initGrammar,
  getGrammarEntry,
  renderStepHr,
  paramKey,
  hrParamOrder,
  type GrammarEntry,
} from '@/converter/catalog-grammar';
import type { StepCatalogEntry } from '@/converter/catalog-types';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const CATALOG_PATH = path.join(REPO_ROOT, 'agent', 'catalogs', 'step-catalog-en.json');
const CORPUS = path.join(REPO_ROOT, 'agent', 'snippet_examples', 'steps');

// Corpus fixtures are keyed by relative path; index by basename for lookup by name.
const fixtures: Record<string, { xml: string }> = JSON.parse(
  readFileSync(path.join(__dirname, 'fixtures', 'xml-to-hr.json'), 'utf-8'),
);
const byStepName = new Map<string, string>();
for (const [rel, fx] of Object.entries(fixtures)) {
  const name = path.basename(rel, '.xml');
  if (!byStepName.has(name)) byStepName.set(name, fx.xml);
}

function loadStep(xml: string): Element {
  const doc = new DOMParser().parseFromString(xml, 'text/xml');
  const step = doc.querySelector('fmxmlsnippet > Step');
  if (!step) throw new Error('no Step in fixture');
  return step as Element;
}

function render(stepName: string): string {
  const xml = byStepName.get(stepName);
  if (!xml) throw new Error(`corpus fixture missing for ${stepName}`);
  const entry = getGrammarEntry(stepName);
  if (!entry) throw new Error(`no catalog entry for ${stepName}`);
  return renderStepHr(entry, loadStep(xml));
}

beforeAll(() => {
  const rawCatalog = JSON.parse(readFileSync(CATALOG_PATH, 'utf-8'));
  const entries: StepCatalogEntry[] = rawCatalog.steps ?? rawCatalog;
  initGrammar(entries);
});

describe('catalog-grammar engine (TS port parity with Python)', () => {
  it('invertedHr boolean → On when state=False', () => {
    expect(render('Change Password')).toBe(
      'Change Password [ Old Password: "old" ; Password: "new" ; With dialog: On ]',
    );
  });

  it('omitWhenEmpty calculation renders WITH its hrLabel (File ID fix)', () => {
    expect(render('Close Data File')).toBe('Close Data File [ File ID: $fileID ]');
    expect(render('Get Data File Position')).toContain('File ID: $fileID');
  });

  it('plain calculation renders bare', () => {
    expect(render('Read from Data File')).toMatch(
      /^Read from Data File \[ File ID: \$fileID ; Amount \(bytes\): 1024/,
    );
  });

  it('parentElement descent reveals nested namedCalcs', () => {
    const out = render('Configure AI Account');
    expect(out).toContain('Account Name: "account_name"');
    expect(out).toContain('API key: "api_key"');
    // ChatGPT is the value FileMaker WRITES; OpenAI is the label it DISPLAYS,
    // and the label is what belongs in HR (measured against FileMaker 26.0.1's
    // Script Workspace). This assertion read "ChatGPT" while the catalog held
    // the label in `enumValues` with no `hrEnumValues` to map it.
    expect(out).toContain('Model Provider: OpenAI');
  });

  it('empty reference token is omitted', () => {
    const out = render('Install OnTimer Script');
    expect(out).not.toContain('""');
    expect(out).toMatch(/^Install OnTimer Script \[ Interval:/);
  });

  it('flagBoolean params render (normalized to boolean at load)', () => {
    const entry = getGrammarEntry('Paste') as GrammarEntry;
    const flags = entry.params.filter((p) => p.raw.type === 'flagBoolean');
    expect(flags.length).toBeGreaterThan(0);
    expect(flags.every((p) => p.type === 'boolean')).toBe(true);
    // Paste's flags are also flagStyle: FileMaker draws them bare, only when on.
    expect(render('Paste')).toBe('Paste [ Select ; No style ; <Table Missing> ]');
  });

  it('governing discriminator reveals companions', () => {
    expect(render('Close Window')).toBe('Close Window [ Current file ]');
  });

  it('hrParamOrder is stable (catalog order) without hrSlot', () => {
    const entry = getGrammarEntry('Change Password') as GrammarEntry;
    expect(hrParamOrder(entry)).toEqual(entry.params.map((_, i) => i));
  });

  it('paramKey uses wrapperElement for a discriminator-revealed namedCalc', () => {
    const entry = getGrammarEntry('Close Window') as GrammarEntry;
    const nameParam = entry.params.find((p) => p.wrapperElement === 'Name');
    expect(nameParam).toBeTruthy();
    expect(paramKey(nameParam!)).toBe('Name');
  });

  it('empty leading positional keeps its slot', () => {
    // A bare field param before a rendered positional token keeps its slot, so
    // the value is not read back into the field's slot. An empty <Field> fills
    // it with FileMaker's own `<Table Missing>`.
    expect(render('Set Next Serial Value')).toBe('Set Next Serial Value [ <Table Missing> ; 100 ]');
    // A governed companion (revealed by a discriminator) is positional too.
    expect(render('Replace Field Contents')).toBe(
      'Replace Field Contents [ With dialog: Off ; <Table Missing> ; "value" ]',
    );
  });

  it('trailing empty positional is dropped', () => {
    const step = loadStep(
      '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="76" name="Set Field">' +
        '<Calculation><![CDATA[]]></Calculation>' +
        '<Field table="Invoices" id="1" name="ID"/></Step></fmxmlsnippet>',
    );
    expect(renderStepHr(getGrammarEntry('Set Field') as GrammarEntry, step)).toBe(
      'Set Field [ Invoices::ID ]',
    );
  });
});
