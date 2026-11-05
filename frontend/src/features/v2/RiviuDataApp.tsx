import { useEffect, useRef } from 'react';
import { ChartColumnIcon, Rows3Icon, UserRoundIcon, WifiOffIcon } from 'lucide-react';
import { BackToTop } from '@/components/ui/back-to-top';
import { TooltipProvider } from '@/components/ui/misc';
import type { Viewer } from '@/lib/auth';
import { cn } from '@/lib/utils';
import { CONNECTION_LOST_TEXT, CONNECTION_RECONNECTING_TEXT, canAdmin, setWorkspaceTab, useApp } from '@/state/app';
import { startRiviuDataApp } from '@/state/riviuData';
import { HistoryDialog } from '@/features/HistoryDialog';
import { CookiesDialog, ProxyDialog } from '@/features/SettingsDialogs';
import { Header } from './Header';
import { LinkTablePanel, ReportContext } from './LinkTable';
import { ExportDialog } from './ExportDialog';
import { AdminWorkspace, ScanCard } from './ScanPanel';
import { ScheduleDialog } from './ScheduleDialog';
import { PosterPanel } from './PosterPanel';
import { SummaryPanel } from './SummaryPanel';

function ConnectionBanner() {
  const state = useApp(s => s.connection);
  if (!state) return null;
  return (
    <div role="alert" className="flex items-center justify-center gap-2 bg-warning/15 px-4 py-1.5 text-center text-xs font-medium text-warning">
      <WifiOffIcon className="size-3.5 shrink-0" />
      {state === 'lost' ? CONNECTION_LOST_TEXT : CONNECTION_RECONNECTING_TEXT}
    </div>
  );
}

const VIEWER_TABS = [
  { key: 'sheet', label: 'Danh sách link', icon: <Rows3Icon /> },
  { key: 'posters', label: 'Báo cáo người đăng', icon: <UserRoundIcon /> },
  { key: 'summary', label: 'Tổng kết', icon: <ChartColumnIcon /> },
] as const;

/** The read-only page: the link list, the per-poster report or the per-partner Tổng kết, for staff and visitors alike. */
function ViewerWorkspace() {
  const stored = useApp(state => state.workspaceTab);
  const tab = stored === 'summary' || stored === 'posters' ? stored : 'sheet';
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-3">
      <nav className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-0.5" role="tablist" aria-label="Các vùng dữ liệu">
        {VIEWER_TABS.map(item => {
          const selected = item.key === tab;
          return (
            <button
              key={item.key}
              type="button"
              role="tab"
              aria-selected={selected}
              onClick={() => setWorkspaceTab(item.key)}
              className={cn(
                'flex h-9 shrink-0 items-center gap-2 rounded-md px-3 text-sm font-medium transition [&_svg]:size-4',
                selected ? 'bg-card text-foreground shadow-sm ring-1 ring-border' : 'text-muted-foreground hover:bg-muted hover:text-foreground',
              )}
            >
              {item.icon}
              {item.label}
            </button>
          );
        })}
      </nav>
      {tab === 'summary' ? <SummaryPanel /> : tab === 'posters' ? <PosterPanel /> : <LinkTablePanel />}
    </div>
  );
}

/** The V2 layout on Riviu Data. Exported for tests (no startup side effects). */
export function RiviuDataLayout({ adminPage }: { adminPage: boolean }) {
  const admin = useApp(state => canAdmin(state));
  const serverMode = useApp(state => state.viewer.serverMode);
  const showAdmin = adminPage && admin;
  return (
    <TooltipProvider>
      <div className="flex min-h-full flex-col" data-page={showAdmin ? 'admin' : 'viewer'}>
        <Header adminPage={showAdmin} scheduleAvailable={showAdmin && serverMode} />
        <ConnectionBanner />
        <main className="flex w-full min-w-0 flex-1 flex-col gap-3 px-4 pb-8 sm:px-6">
          <ReportContext />
          {showAdmin ? (
            <>
              <ScanCard />
              <AdminWorkspace />
            </>
          ) : <ViewerWorkspace />}
        </main>
        <BackToTop />
        <ExportDialog />
        <HistoryDialog />
        {showAdmin && (
          <>
            <ScheduleDialog />
            <ProxyDialog />
            <CookiesDialog />
          </>
        )}
      </div>
    </TooltipProvider>
  );
}

export function RiviuDataApp({ viewer, adminPage }: { viewer: Viewer; adminPage: boolean }) {
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void startRiviuDataApp(viewer);
  }, [viewer]);
  useEffect(() => {
    document.title = adminPage ? 'Quản trị · Riviu Reports' : 'Riviu Reports';
  }, [adminPage]);
  return <RiviuDataLayout adminPage={adminPage} />;
}
