import { useEffect, useRef } from 'react';
import { WifiOffIcon } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/misc';
import type { Viewer } from '@/lib/auth';
import { CONNECTION_LOST_TEXT, CONNECTION_RECONNECTING_TEXT, canAdmin, useApp } from '@/state/app';
import { startRiviuDataApp } from '@/state/riviuData';
import { HistoryDialog } from '@/features/HistoryDialog';
import { CookieDialog, ProxyDialog } from '@/features/SettingsDialogs';
import { Header } from './Header';
import { LinkTablePanel, ReportContext } from './LinkTable';
import { ExportDialog } from './ExportDialog';
import { AdminWorkspace, ScanCard } from './ScanPanel';
import { ScheduleDialog } from './ScheduleDialog';

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
        <main className="mx-auto flex w-full min-w-0 max-w-[1440px] flex-1 flex-col gap-3 px-4 pb-8 sm:px-6">
          <ReportContext />
          {showAdmin ? (
            <>
              <ScanCard />
              <AdminWorkspace />
            </>
          ) : <LinkTablePanel />}
        </main>
        <ExportDialog />
        <HistoryDialog />
        {showAdmin && (
          <>
            <ScheduleDialog />
            <ProxyDialog />
            <CookieDialog kind="threads" />
            <CookieDialog kind="tiktok" />
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
