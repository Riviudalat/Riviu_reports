import { describe, expect, it } from 'vitest';
import { formatDuration, formatNumber, formatResultNumber, sumKnown, filenameFromDisposition } from './format';

describe('metric formatting', () => {
  it('shows unknown metrics blank and a confirmed zero as 0', () => {
    expect(formatResultNumber(null)).toBe('');
    expect(formatResultNumber(undefined)).toBe('');
    expect(formatResultNumber('')).toBe('');
    expect(formatResultNumber(0)).toBe('0');
    expect(formatResultNumber('0')).toBe('0');
  });

  it('groups thousands the Vietnamese way', () => {
    expect(formatResultNumber(1234567)).toBe('1.234.567');
    expect(formatNumber(1000)).toBe('1.000');
  });
});

describe('sumKnown (totals)', () => {
  it('stays blank only when nothing in the column is known', () => {
    expect(sumKnown([null, undefined, ''])).toBeNull();
    expect(formatResultNumber(sumKnown([null, '']))).toBe('');
  });

  it('adds known values and skips unknown ones', () => {
    expect(sumKnown([10, null, '', 5, undefined])).toBe(15);
    expect(sumKnown(['1.200', 300])).toBe(1500);
  });

  it('keeps a column of confirmed zeros at 0, not blank', () => {
    expect(sumKnown([0, null, 0])).toBe(0);
    expect(formatResultNumber(sumKnown([0, null]))).toBe('0');
  });
});

describe('misc formatting', () => {
  it('formats durations', () => {
    expect(formatDuration(45)).toBe('45s');
    expect(formatDuration(125)).toBe('2m 5s');
    expect(formatDuration(3720)).toBe('1h 2m');
    expect(formatDuration(null)).toBe('');
  });

  it('reads UTF-8 download filenames', () => {
    expect(filenameFromDisposition("attachment; filename*=UTF-8''B%C3%A1o%20c%C3%A1o.xlsx", 'x')).toBe('Báo cáo.xlsx');
    expect(filenameFromDisposition('attachment; filename="a.zip"', 'x')).toBe('a.zip');
    expect(filenameFromDisposition('', 'fallback.xlsx')).toBe('fallback.xlsx');
  });
});
