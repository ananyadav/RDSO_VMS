/** Strip accidental POSIX+Windows path concatenation for display/edit. */
export function sanitizeRecordingsPath(raw: string): string {
  const text = (raw || '').trim();
  const win = text.match(/[A-Za-z]:[\\/].+/);
  if (win && text.includes('/')) {
    return win[0];
  }
  return text;
}
