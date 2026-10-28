import { useQuery } from '@tanstack/react-query';
import { RefreshCwIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { EmptyRow, tableClass, tdClass, thClass } from '@/components/common';
import { getJson } from '@/lib/api';
import { formatDuration, formatNumber, formatResultNumber } from '@/lib/format';
import { cn } from '@/lib/utils';
import { closeModal, useApp } from '@/state/app';

const SCRAPE_HISTORY_LIMIT = 50;

interface HistoryEntry {
  timestamp?: string;
  fileLabel?: string;
  scanSheet?: string;
  scrapedUrls?: number;
  scrapedRows?: number;
  totalLinks?: number;
  totalViews?: number | null;
  totalLikes?: number | null;
  totalComments?: number | null;
  totalSaves?: number | null;
  totalShares?: number | null;
  durationSeconds?: number;
}

async function fetchHistory(): Promise<HistoryEntry[]> {
  const { response, data } = await getJson<{ history?: HistoryEntry[]; error?: string }>(`/scrape-history?limit=${SCRAPE_HISTORY_LIMIT}`);
  if (!response.ok) throw new Error(data.error || 'Không tải được lịch sử');
  return Array.isArray(data.history) ? data.history : [];
}

export function HistoryDialog() {
  const open = useApp(state => state.modal === 'history');
  const query = useQuery({ queryKey: ['scrape-history'], queryFn: fetchHistory, enabled: open, staleTime: 0 });
  const history = query.data || [];
  const subtitle = query.isError ? 'Không tải được lịch sử.' : query.isSuccess ? (history.length ? `${history.length} phiên gần nhất` : 'Chưa có phiên quét nào.') : 'Số liệu theo từng phiên quét (link và metrics của các dòng đã quét).';
  const right = cn(thClass, 'text-right');
  return (
    <Dialog open={open} onOpenChange={value => !value && closeModal()}>
      <DialogContent className="max-w-6xl">
        <DialogHeader>
          <DialogTitle>Lịch sử quét</DialogTitle>
          <DialogDescription>{subtitle}</DialogDescription>
        </DialogHeader>
        <DialogBody className="p-0">
          <div className="max-h-[65vh] overflow-auto">
            <table className={tableClass}>
              <thead>
                <tr>
                  <th className={thClass}>Thời gian</th><th className={thClass}>File</th><th className={thClass}>Tên sheet</th>
                  <th className={right}>Link đã quét</th><th className={right}>Dòng xử lý</th><th className={right}>View (phiên)</th>
                  <th className={right}>Tim</th><th className={right}>Bình luận</th><th className={right}>Lưu</th><th className={right}>Chia sẻ</th><th className={right}>Thời lượng</th>
                </tr>
              </thead>
              <tbody>
                {query.isPending && <EmptyRow colSpan={11}>Đang tải lịch sử...</EmptyRow>}
                {query.isError && <EmptyRow colSpan={11} tone="error">{query.error.message}</EmptyRow>}
                {query.isSuccess && history.length === 0 && <EmptyRow colSpan={11}>Chưa có phiên quét nào.</EmptyRow>}
                {history.map((entry, index) => {
                  const links = entry.scrapedUrls ?? entry.scrapedRows ?? entry.totalLinks ?? 0;
                  const rows = entry.scrapedRows ?? links;
                  const num = cn(tdClass, 'text-right tabular-nums');
                  return (
                    <tr key={`${entry.timestamp}-${index}`} className="hover:bg-muted/40">
                      <td className={cn(tdClass, 'whitespace-nowrap')}>{entry.timestamp || ''}</td>
                      <td className={cn(tdClass, 'max-w-64 truncate')} title={entry.fileLabel}>{entry.fileLabel || ''}</td>
                      <td className={tdClass}>{entry.scanSheet || '—'}</td>
                      <td className={num}>{formatNumber(links)}</td>
                      <td className={num}>{formatNumber(rows)}</td>
                      <td className={num}>{formatResultNumber(entry.totalViews)}</td>
                      <td className={num}>{formatResultNumber(entry.totalLikes)}</td>
                      <td className={num}>{formatResultNumber(entry.totalComments)}</td>
                      <td className={num}>{formatResultNumber(entry.totalSaves)}</td>
                      <td className={num}>{formatResultNumber(entry.totalShares)}</td>
                      <td className={num}>{formatDuration(entry.durationSeconds || 0)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="soft" onClick={() => void query.refetch()} disabled={query.isFetching}><RefreshCwIcon className={query.isFetching ? 'animate-spin' : ''} /> Làm mới</Button>
          <Button variant="outline" onClick={closeModal}>Đóng</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
