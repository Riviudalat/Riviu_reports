import { describe, expect, it } from 'vitest';
import { buildExportFallbackFilename, normalizeReportPartners, partnerLinkStatusText, reportFilterParams } from './partners';

describe('partnerLinkStatusText', () => {
  it('says the partner has no link only when it really has none', () => {
    expect(partnerLinkStatusText({ rawLinkCount: 0, linkCount: 0 })).toBe('Chưa có link');
    expect(partnerLinkStatusText({ rawLinkCount: null, linkCount: null })).toBe('Chưa có link');
  });

  it('is empty when the partner has exportable links', () => {
    expect(partnerLinkStatusText({ rawLinkCount: 4, linkCount: 2 })).toBe('');
    // A legacy payload without rawLinkCount but with exportable links.
    expect(partnerLinkStatusText({ rawLinkCount: 0, linkCount: 3 })).toBe('');
  });

  it('counts the links that have no metrics or sit under the view threshold', () => {
    expect(partnerLinkStatusText({ rawLinkCount: 3, linkCount: 0 })).toBe('3 link · chưa có số liệu hoặc dưới ngưỡng view');
  });
});

describe('partner helpers', () => {
  it('normalizes string and object partners and drops blank names', () => {
    expect(normalizeReportPartners(['A', { name: ' B ', linkCount: '2', rawLinkCount: 5 }, { name: '' }])).toEqual([
      { name: 'A', linkCount: null, rawLinkCount: null },
      { name: 'B', linkCount: 2, rawLinkCount: 5 },
    ]);
  });

  it('falls back to 100 views for an invalid threshold', () => {
    expect(reportFilterParams(true, 'abc')).toEqual({ applyMinViews: true, minViews: 100 });
    expect(reportFilterParams(false, '-5')).toEqual({ applyMinViews: false, minViews: 100 });
    expect(reportFilterParams(true, '0')).toEqual({ applyMinViews: true, minViews: 0 });
  });

  it('names single-partner exports .xlsx and multi-partner exports .zip', () => {
    const now = new Date(2026, 9, 27, 9, 5);
    expect(buildExportFallbackFilename(['Cổ Làng Mơ'], 'tháng 9', now)).toBe('Cổ Làng Mơ tháng 9 27-10-2026-09-05.xlsx');
    expect(buildExportFallbackFilename(['A', 'B'], 'tháng 9', now)).toBe('bao_cao_doi_tac tháng 9 27-10-2026-09-05.zip');
  });
});
