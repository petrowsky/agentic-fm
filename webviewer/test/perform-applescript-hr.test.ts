/**
 * Perform AppleScript's mode read from the token's shape.
 *
 * FileMaker prints no ContentType token for Perform AppleScript. Text mode shows the
 * literal straight-quoted and unescaped (`Perform AppleScript [ "a "b";c] d" ]`),
 * Calculation mode shows the calculation as written, and an empty step of either
 * mode shows `[ ]` and copies back with no `<Text>` or `<Calculation>`. The catalog
 * models ContentType as a governing discriminator whose two values each reveal one
 * bare companion (`shapeRoutedValues`); the text is `hrQuoted` with
 * `hrQuoteStyle: "straight"`. The (text, FileMaker's line) pairs below were pasted
 * into FileMaker Pro 26.0.3 and read back. Mirrors
 * agent/scripts/test_perform_applescript_hr.py.
 */
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { hrToXml, loadCatalog } from '@/converter/hr-to-xml';
import { xmlToHr } from '@/converter/xml-to-hr';
import { readsAsQuotedLiteralText } from '@/converter/calc-literal';
import type { StepCatalogEntry } from '@/converter/catalog-types';

const REPO_ROOT = path.resolve(__dirname, '..', '..');
const rawCatalog = JSON.parse(
  readFileSync(path.join(REPO_ROOT, 'agent', 'catalogs', 'step-catalog-en.json'), 'utf-8'),
);
const entries: StepCatalogEntry[] = rawCatalog.steps ?? rawCatalog;
loadCatalog(entries);

const LQ = '“';
const esc = (s: string) =>
  s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

// [stored text, FileMaker's displayed line, our line]
const CASES: Array<[string, string, string]> = [
  ['tell app "Finder" to activate', 'Perform AppleScript [ "tell app "Finder" to activate" ]',
    'Perform AppleScript [ "tell app \\"Finder\\" to activate" ]'],
  ['a "b".txt', 'Perform AppleScript [ "a "b".txt" ]', 'Perform AppleScript [ "a \\"b\\".txt" ]'],
  ['file:a;b.txt', 'Perform AppleScript [ "file:a;b.txt" ]', 'Perform AppleScript [ "file:a;b.txt" ]'],
  ['file:a]b.txt', 'Perform AppleScript [ "file:a]b.txt" ]', 'Perform AppleScript [ "file:a]b.txt" ]'],
  ['file:a ' + LQ + 'b.txt', 'Perform AppleScript [ "file:a ' + LQ + 'b.txt" ]',
    'Perform AppleScript [ "file:a \\' + LQ + 'b.txt" ]'],
  ['a "b', 'Perform AppleScript [ "a "b" ]', 'Perform AppleScript [ "a \\"b" ]'],
  ['a "b";c] d', 'Perform AppleScript [ "a "b";c] d" ]', 'Perform AppleScript [ "a \\"b\\";c] d" ]'],
  ['a\\b', 'Perform AppleScript [ "a\\b" ]', 'Perform AppleScript [ "a\\b" ]'],
  ['say "hi"', 'Perform AppleScript [ "say "hi"" ]', 'Perform AppleScript [ "say \\"hi\\"" ]'],
  ['  x  ', 'Perform AppleScript [ "  x  " ]', 'Perform AppleScript [ "  x  " ]'],
];

const textXml = (text: string) =>
  '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="67" name="Perform AppleScript">' +
  '<ContentType value="Text"/>' + (text ? '<Text>' + esc(text) + '</Text>' : '') + '</Step></fmxmlsnippet>';
const calcXml = (calc: string) =>
  '<fmxmlsnippet type="FMObjectList"><Step enable="True" id="67" name="Perform AppleScript">' +
  '<ContentType value="Calculation"/><Calculation><![CDATA[' + calc + ']]></Calculation></Step></fmxmlsnippet>';

describe('Perform AppleScript HR', () => {
  it.each(CASES)('Text mode renders straight-quoted and round-trips (%s)', (text, _fm, ours) => {
    expect(xmlToHr(textXml(text))).toBe(ours);
    const out = hrToXml(ours).xml;
    expect(out).toContain('<ContentType value="Text"/>');
    expect(out).toContain('<Text>' + esc(text) + '</Text>');
    expect(out).not.toContain('<Calculation>');
  });

  it.each(CASES)("FileMaker's line reads as the stored text (%s)", (text, fmLine) => {
    const out = hrToXml(fmLine).xml;
    expect(out).toContain('<ContentType value="Text"/>');
    expect(out).toContain('<Text>' + esc(text) + '</Text>');
    expect(out).not.toContain('<Calculation>');
  });

  it.each(['"say " & Quote ( $x ) & " ; done"', '$script', 'Upper ( "a" ) & "b"'])(
    'Calculation mode renders bare and round-trips (%s)',
    (calc) => {
      expect(xmlToHr(calcXml(calc))).toBe('Perform AppleScript [ ' + calc + ' ]');
      const out = hrToXml('Perform AppleScript [ ' + calc + ' ]').xml;
      expect(out).toContain('<ContentType value="Calculation"/>');
      expect(out).toContain('<![CDATA[' + calc + ']]>');
      expect(out).not.toContain('<Text>');
    },
  );

  it('a single-literal calculation names its mode', () => {
    const calc = '"tell application \\"Notes\\" to open"';
    expect(xmlToHr(calcXml(calc))).toBe('Perform AppleScript [ Calculation ; ' + calc + ' ]');
    const out = hrToXml('Perform AppleScript [ Calculation ; ' + calc + ' ]').xml;
    expect(out).toContain('<ContentType value="Calculation"/>');
    expect(out).toContain('<![CDATA[' + calc + ']]>');
    const fm = hrToXml('Perform AppleScript [ ' + calc + ' ]').xml;
    expect(fm).toContain('<Text>tell application &quot;Notes&quot; to open</Text>');
  });

  it('an empty step reads as the default mode with nothing in it', () => {
    expect(xmlToHr(textXml(''))).toBe('Perform AppleScript [ ]');
    const out = hrToXml('Perform AppleScript [ ]').xml;
    expect(out).toContain('<ContentType value="Calculation"/>');
    expect(out).not.toContain('<Calculation>');
    expect(out).not.toContain('<Text>');
  });

  it('tells quoted literal text from a calculation', () => {
    for (const t of ['"x"', '"a \\"b\\""', '"a "b";c] d"', '"say "hi""', '"a "b"', '"x"  "y"',
      LQ + 'x”', '"x" ; "y"']) {
      expect(readsAsQuotedLiteralText(t), t).toBe(true);
    }
    for (const t of ['$x', '"a" & $x', '"say " & $x & " done"', '"a" & Upper ( "b" ) & "c"',
      '"a" and "b"', 'Upper ( "a" )', '"a" ＆ "b"', '"a" & List ( "b" ; "c" )', '']) {
      expect(readsAsQuotedLiteralText(t), t).toBe(false);
    }
  });
});
