import type { FMContext } from '@/context/types';

export interface UnresolvedRef {
  type: 'layout' | 'field' | 'script';
  name: string;
}

/**
 * Resolves names to IDs using CONTEXT.json data.
 * Falls back to returning 0 when no match is found,
 * and tracks the unresolved reference for reporting.
 */
export interface IdResolver {
  resolveLayout(name: string): { id: number; name: string };
  resolveField(tableField: string): { table: string; fieldId: number; fieldName: string };
  resolveScript(name: string): { id: number; name: string };
  /** Returns all references that could not be resolved (id=0) */
  getUnresolved(): UnresolvedRef[];
  /** Records a conversion warning raised while emitting a step */
  addWarning(line: number, message: string): void;
  /** Returns the conversion warnings recorded so far */
  getWarnings(): { line: number; message: string }[];
}

export function createIdResolver(context: FMContext | null): IdResolver {
  const unresolved: UnresolvedRef[] = [];
  const warnings: { line: number; message: string }[] = [];

  return {
    resolveLayout(name: string) {
      if (!context?.layouts) {
        unresolved.push({ type: 'layout', name });
        return { id: 0, name };
      }
      const layout = context.layouts[name];
      if (layout) return { id: layout.id, name };
      // Case-insensitive search
      for (const [lName, lData] of Object.entries(context.layouts)) {
        if (lName.toLowerCase() === name.toLowerCase()) {
          return { id: lData.id, name: lName };
        }
      }
      unresolved.push({ type: 'layout', name });
      return { id: 0, name };
    },

    resolveField(tableField: string) {
      const sep = tableField.indexOf('::');
      if (sep < 0) {
        unresolved.push({ type: 'field', name: tableField });
        return { table: '', fieldId: 0, fieldName: tableField };
      }

      const table = tableField.substring(0, sep).trim();
      const field = tableField.substring(sep + 2).trim();

      if (!context?.tables) {
        unresolved.push({ type: 'field', name: tableField });
        return { table, fieldId: 0, fieldName: field };
      }

      // Search tables by TO name (key or .to field)
      for (const [toKey, tData] of Object.entries(context.tables)) {
        const toName = tData.to ?? toKey;
        if (toKey === table || toName === table
            || toKey.toLowerCase() === table.toLowerCase()
            || toName.toLowerCase() === table.toLowerCase()) {
          const fData = tData.fields[field];
          if (fData) return { table: tData.to, fieldId: fData.id, fieldName: field };
          // Case-insensitive field search
          for (const [fName, fInfo] of Object.entries(tData.fields)) {
            if (fName.toLowerCase() === field.toLowerCase()) {
              return { table: tData.to, fieldId: fInfo.id, fieldName: fName };
            }
          }
          // Found table but not field
          unresolved.push({ type: 'field', name: tableField });
          return { table: tData.to, fieldId: 0, fieldName: field };
        }
      }

      unresolved.push({ type: 'field', name: tableField });
      return { table, fieldId: 0, fieldName: field };
    },

    resolveScript(name: string) {
      if (!context?.scripts) {
        unresolved.push({ type: 'script', name });
        return { id: 0, name };
      }
      const script = context.scripts[name];
      if (script) return { id: script.id, name };
      // Case-insensitive
      for (const [sName, sData] of Object.entries(context.scripts)) {
        if (sName.toLowerCase() === name.toLowerCase()) {
          return { id: sData.id, name: sName };
        }
      }
      unresolved.push({ type: 'script', name });
      return { id: 0, name };
    },

    getUnresolved() {
      return unresolved;
    },

    addWarning(line: number, message: string) {
      warnings.push({ line, message });
    },

    getWarnings() {
      return warnings;
    },
  };
}
