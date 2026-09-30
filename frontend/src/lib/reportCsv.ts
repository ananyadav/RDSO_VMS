/** Client CSV helpers for RDSO 18.1.15 reports (no secrets). */

export function escapeCsvCell(value: unknown): string {
  if (value == null) return '';
  const text = String(value);
  if (/[",\r\n]/.test(text)) {
    return `"${text.replace(/"/g, '""')}"`;
  }
  return text;
}

export function rowsToCsv(rows: Record<string, unknown>[], fields: string[]): string {
  const header = fields.map(escapeCsvCell).join(',');
  const lines = rows.map((row) => fields.map((f) => escapeCsvCell(row[f])).join(','));
  return [header, ...lines].join('\r\n');
}

export function downloadCsv(filename: string, csvBody: string): void {
  const blob = new Blob([csvBody], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.rel = 'noopener';
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}
