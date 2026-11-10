/**
 * Human-Readable -> fmxmlsnippet XML converter.
 *
 * Parses HR script text line-by-line and emits valid fmxmlsnippet XML.
 */

import { parseScript, parseLine } from './parser';
import { getHrToXmlConverter, stepOpen, stepSelfClose, cdata, escXml } from './step-registry';
import type { IdResolver } from './id-resolver';
import { createIdResolver } from './id-resolver';
import type { FMContext } from '@/context/types';
import type { StepCatalogEntry } from './catalog-types';
import { registerCatalogConverters } from './catalog-converter';
import { initGrammar } from './catalog-grammar';

// Control-flow hand-coders (the sanctioned catalog exception) — side-effect
// import, must come before catalog registration so they win over the engine.
// Every other step (data steps) is now rendered from the catalog grammar by the
// shared engine; their former steps/*.ts hand-coders were retired in P6.3.
import './steps/control';

let catalogLoaded = false;

/** Load catalog entries into both converter registries (idempotent). */
export function loadCatalog(catalog: StepCatalogEntry[]): void {
  if (catalogLoaded) return;
  catalogLoaded = true;
  initGrammar(catalog); // grammar registry (catalog-grammar.ts) — powers both directions
  registerCatalogConverters(catalog); // HR→XML: engine for every non-control step
}

export interface ConversionResult {
  xml: string;
  errors: ConversionError[];
  warnings: ConversionError[];
}

export interface ConversionError {
  line: number;
  message: string;
}

/**
 * Convert human-readable FileMaker script text to fmxmlsnippet XML.
 */
export function hrToXml(hrText: string, context?: FMContext | null): ConversionResult {
  // Trim trailing blank lines so they don't produce extra empty comments
  const trimmedText = hrText.replace(/\n+$/, '');
  const lines = parseScript(trimmedText);
  const resolver = createIdResolver(context ?? null);
  const errors: ConversionError[] = [];
  const parseWarnings: ConversionError[] = [];
  const stepXmls: string[] = [];

  for (const line of lines) {
    // Empty lines become empty # (comment) steps. The step id must be the
    // catalog id for a comment (89), not 0: a Step-level id="0" fails snippet
    // validation and forces FileMaker to resolve the step by its (localized)
    // name on paste, which is fragile on non-English builds. stepSelfClose
    // resolves the id from the catalog, so it stays correct and locale-independent.
    if (!line.stepName) {
      stepXmls.push(stepSelfClose('# (comment)', !line.disabled));
      continue;
    }

    // A comment's continuation line (one indented past the comment) is part of
    // its text. Say so when that line reads as a step: it is the same text an
    // author gets by under-indenting a comment above a deeper step, and folding
    // a step into a comment must not be silent.
    if (line.isComment) {
      line.raw.split('\n').slice(1).forEach((physical, i) => {
        const as = parseLine(physical, line.lineNumber + i + 1);
        if (!as.isComment && as.stepName && getHrToXmlConverter(as.stepName)) {
          parseWarnings.push({
            line: as.lineNumber,
            message: `Line ${as.lineNumber} is indented under the comment on line ${line.lineNumber}, so it was kept as part of that comment, though it reads as "${as.stepName}". If it is a step, indent it to the comment's level.`,
          });
        }
      });
    }

    // A disclosed fallback: the step's `]` was found only by reading an
    // unbalanced quote as FileMaker's display form (parser.ts).
    if (line.displayFormQuote) {
      parseWarnings.push({
        line: line.lineNumber,
        message: `Unbalanced quote at line ${line.lineNumber} in "${line.stepName} [...]": read the way FileMaker displays text, so the step ends at the ] on that line. Escape a quote inside text with a backslash (\\") to make it unambiguous.`,
      });
    }
    if (line.unterminatedCalcComment) {
      parseWarnings.push({
        line: line.lineNumber,
        message: `Unclosed /* comment in "${line.stepName} [...]": FileMaker rejects this calculation; the /* was read as plain text.`,
      });
    }

    const converter = getHrToXmlConverter(line.stepName);
    if (converter) {
      try {
        stepXmls.push(converter.toXml(line, resolver));
      } catch (err) {
        errors.push({
          line: line.lineNumber,
          message: `Error converting "${line.stepName}": ${err}`,
        });
        // Emit a comment with the error
        stepXmls.push(unknownStepXml(line.stepName, line.raw.trim(), !line.disabled));
      }
    } else {
      // Unknown step — emit as comment placeholder
      errors.push({
        line: line.lineNumber,
        message: `Unknown step: "${line.stepName}"`,
      });
      stepXmls.push(unknownStepXml(line.stepName, line.raw.trim(), !line.disabled));
    }
  }

  // Report unresolved references as warnings (not errors).
  // The XML is still valid — FileMaker resolves by name on paste.
  const warnings: ConversionError[] = [...parseWarnings, ...resolver.getWarnings()];
  for (const ref of resolver.getUnresolved()) {
    warnings.push({
      line: 0,
      message: `Unresolved ${ref.type}: "${ref.name}" (id will be 0)`,
    });
  }

  const xml = [
    '<?xml version="1.0"?>',
    '<fmxmlsnippet type="FMObjectList">',
    ...stepXmls,
    '</fmxmlsnippet>',
  ].join('\n');

  return { xml, errors, warnings };
}

/** Generate a comment step for unrecognized steps */
function unknownStepXml(stepName: string, rawText: string, enabled: boolean): string {
  return [
    stepOpen('# (comment)', enabled),
    `    <Text>[UNKNOWN STEP: ${escXml(stepName)}] ${escXml(rawText)}</Text>`,
    '  </Step>',
  ].join('\n');
}

export { createIdResolver };
