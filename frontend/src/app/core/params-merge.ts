export function parseValue(text: string): unknown {
  const t = String(text).trim();
  if (t === '') return '';
  try {
    return JSON.parse(t);
  } catch {
    return t;
  }
}

export function cloneValue(v: unknown): unknown {
  return JSON.parse(JSON.stringify(v));
}

export function getParts(path: string): string[] {
  return path.replace(/^\$/, '').split('.').filter((s) => s !== '');
}

function setAt(obj: Record<string, unknown>, parts: string[], value: unknown): Record<string, unknown> {
  if (!parts.length) throw new Error('setAt requires a non-empty path');
  let cur = obj as Record<string, unknown>;
  for (let i = 0; i < parts.length - 1; i++) {
    // `getParts` no deja segmentos vacíos, así que el `?? ''` no cambia nada:
    // sólo le dice al type checker que el índice no es `undefined`.
    const seg = parts[i] ?? '';
    const nextIsIndex = /^\d+$/.test(parts[i + 1] ?? '');
    if (cur[seg] == null || typeof cur[seg] !== 'object') {
      cur[seg] = nextIsIndex ? [] : {};
    }
    cur = cur[seg] as Record<string, unknown>;
  }
  cur[parts[parts.length - 1] ?? ''] = value;
  return obj;
}

function deleteAt(obj: Record<string, unknown>, parts: string[]): Record<string, unknown> {
  if (!parts.length) return {};
  let cur: unknown = obj;
  for (let i = 0; i < parts.length - 1; i++) {
    const seg = parts[i] ?? '';
    if (cur == null || (cur as Record<string, unknown>)[seg] == null) return obj;
    cur = (cur as Record<string, unknown>)[seg];
  }
  const last = parts[parts.length - 1] ?? '';
  if (Array.isArray(cur) && /^\d+$/.test(last)) {
    cur.splice(Number(last), 1);
  } else {
    delete (cur as Record<string, unknown>)[last];
  }
  return obj;
}

export interface ChangeRow {
  path: string;
  op: string;
  old: unknown;
  text: string;
  include: boolean;
}

export function mergedValue(rows: ChangeRow[], isJSON: boolean, base: unknown) {
  const selected = rows.filter((r) => r.include);
  if (!isJSON) {
    const whole = selected.find((r) => r.path === '$');
    return whole ? parseValue(whole.text) : '';
  }
  const out = cloneValue(base ?? ('' as unknown)) as Record<string, unknown>;
  const dels = selected
    .filter((r) => r.op === 'del')
    .map((r) => getParts(r.path))
    .sort((a, b) => {
      const la = a[a.length - 1] || '';
      const lb = b[b.length - 1] || '';
      const na = /^\d+$/.test(la);
      const nb = /^\d+$/.test(lb);
      if (na && nb) return Number(lb) - Number(la);
      return 0;
    });
  for (const parts of dels) deleteAt(out, parts);
  for (const r of selected) {
    if (r.op !== 'del') setAt(out, getParts(r.path), parseValue(r.text));
  }
  return out;
}

export function serializeMerged(rows: ChangeRow[], isJSON: boolean, base: unknown): string {
  const v = mergedValue(rows, isJSON, base);
  return isJSON ? JSON.stringify(v, null, 2) : String(v);
}