import { describe, expect, it } from 'vitest';
import { escapeCsvCell, rowsToCsv } from './reportCsv';

describe('reportCsv', () => {
  it('escapes commas and quotes', () => {
    expect(escapeCsvCell('a,b')).toBe('"a,b"');
    expect(escapeCsvCell('say "hi"')).toBe('"say ""hi"""');
  });

  it('builds csv with header and rows', () => {
    const csv = rowsToCsv(
      [{ event_id: '1', title: 'Motion, gate', alarm_state: 'open' }],
      ['event_id', 'title', 'alarm_state'],
    );
    expect(csv.split('\r\n')[0]).toBe('event_id,title,alarm_state');
    expect(csv).toContain('"Motion, gate"');
  });

  it('empty rows still emit header', () => {
    const csv = rowsToCsv([], ['a', 'b']);
    expect(csv).toBe('a,b');
  });
});
