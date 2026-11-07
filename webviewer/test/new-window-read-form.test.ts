/**
 * New Window read from the line FileMaker draws for it.
 *
 * FileMaker draws a named layout as `Using layout: “Name” (TO)`. The catalog's
 * value-less `hrLabelWhen` variant takes that label as input, the layout token
 * drops its curly quotes and table occurrence, and the step is the one
 * `Layout: "Name"` writes. Mirrors agent/scripts/test_catalog_emit.py.
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

describe('New Window read form', () => {
  it('takes the line FileMaker draws', () => {
    const written = hrToXml('New Window [ Style: Document ; Layout: "Clients" ]').xml;
    expect(written).toContain('name="Clients"');
    expect(hrToXml('New Window [ Style: Document ; Using layout: “Clients” (Primary) ]').xml).toBe(written);
  });
});
