import type { ParsedLine } from './parser';
import type { IdResolver } from './id-resolver';
import { resolveStepId } from './catalog-grammar';

/**
 * Step converter interface.
 * Each step type implements HR -> XML and XML -> HR conversion.
 */
export interface StepConverter {
  /** HR step name(s) this converter handles */
  stepNames: string[];
  /** Convert a parsed HR line to fmxmlsnippet XML Step element */
  toXml(line: ParsedLine, resolver: IdResolver): string;
}

export interface XmlStepConverter {
  /** XML step name attribute this converter handles */
  xmlStepNames: string[];
  /** Convert an XML Step element to HR text */
  toHR(stepEl: Element): string;
}

/** Registry of HR -> XML converters */
const hrToXmlRegistry = new Map<string, StepConverter>();

/** Registry of XML -> HR converters */
const xmlToHrRegistry = new Map<string, XmlStepConverter>();

export function registerHrToXml(converter: StepConverter): void {
  for (const name of converter.stepNames) {
    hrToXmlRegistry.set(name, converter);
  }
}

export function registerXmlToHr(converter: XmlStepConverter): void {
  for (const name of converter.xmlStepNames) {
    xmlToHrRegistry.set(name, converter);
  }
}

export function getHrToXmlConverter(stepName: string): StepConverter | undefined {
  return hrToXmlRegistry.get(stepName);
}

export function getXmlToHrConverter(xmlStepName: string): XmlStepConverter | undefined {
  return xmlToHrRegistry.get(xmlStepName);
}

/**
 * XML helper: escape special chars for XML content. A carriage return is written
 * as `&#13;` (FileMaker's own serialization): XML parsing turns a raw CR into LF,
 * and FileMaker stores CR and LF as different characters, so a raw CR in a path
 * list or text param pastes back as LF.
 */
export function escXml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/\r/g, '&#13;');
}

/**
 * XML helper: wrap in CDATA, splitting each "]]>" across two sections.
 * "]]>" ends a CDATA section, so an unsplit one leaves malformed XML that
 * FileMaker refuses to paste; FileMaker joins the sections back into one value.
 */
export function cdata(s: string): string {
  return `<![CDATA[${s.split(']]>').join(']]]]><![CDATA[>')}]]>`;
}

/** XML helper: create a Step opening tag (with FM's universal step-type id). */
export function stepOpen(name: string, enabled = true): string {
  return `  <Step enable="${enabled ? 'True' : 'False'}" id="${resolveStepId(name)}" name="${escXml(name)}">`;
}

/** XML helper: create a self-closing Step tag (with FM's universal step-type id). */
export function stepSelfClose(name: string, enabled = true): string {
  return `  <Step enable="${enabled ? 'True' : 'False'}" id="${resolveStepId(name)}" name="${escXml(name)}"/>`;
}
