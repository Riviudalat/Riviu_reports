import { useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Loader2Icon, WifiOffIcon } from 'lucide-react';
import { TooltipProvider } from '@/components/ui/misc';
import { fetchViewer, type Viewer } from '@/lib/auth';
import { CONNECTION_LOST_TEXT, CONNECTION_RECONNECTING_TEXT, startApp, useApp } from '@/state/app';
import { TopBar } from '@/features/TopBar';
import { PlatformBar, ScanToolbar } from '@/features/ScanToolbar';
import { ProgressSection, WorkspacePanel, WorkspaceTabs } from '@/features/Workspace';
import { SourceDrawer } from '@/features/SourceDrawer';
import { HistoryDialog } from '@/features/HistoryDialog';
import { ReportDialog } from '@/features/ReportDialog';
import { GuideDialog } from '@/features/GuideDialog';
import { CookieDialog, GooglePasteDialog, ProxyDialog } from '@/features/SettingsDialogs';
import { LoginPage } from '@/features/LoginPage';
import { useIsAdmin } from '@/components/common';
import { fetchVersion, isAdminPage } from '@/lib/version';
import { RiviuDataApp } from '@/features/v2/RiviuDataApp';

function ConnectionBanner() {
  const state = useApp(s => s.connection);
  if (!state) return null;
  return (
    <div role="alert" className="flex items-center justify-center gap-2 bg-warning/15 px-4 py-1.5 text-center text-xs font-medium text-warning">
      <WifiOffIcon className="size-3.5" />
      {state === 'lost' ? CONNECTION_LOST_TEXT : CONNECTION_RECONNECTING_TEXT}
    </div>
  );
}

/** The workspace: everything below the top bar. Exported for tests (no startup side effects). */
export function MainLayout() {
  const admin = useIsAdmin();
  return (
    <TooltipProvider>
      <div className="flex min-h-full flex-col">
        <TopBar />
        <ConnectionBanner />
        <main className="mx-auto flex w-full max-w-[1600px] flex-1 flex-col gap-3 px-4 py-4">
          <div className="flex flex-col gap-3 rounded-xl border bg-card p-3 shadow-xs">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <PlatformBar />
            </div>
            <ScanToolbar />
          </div>
          <WorkspaceTabs />
          <ProgressSection />
          <WorkspacePanel />
        </main>
        <SourceDrawer />
        <HistoryDialog />
        <ReportDialog />
        <GuideDialog />
        {admin && (
          <>
            <ProxyDialog />
            <CookieDialog kind="threads" />
            <CookieDialog kind="tiktok" />
            <GooglePasteDialog />
          </>
        )}
      </div>
    </TooltipProvider>
  );
}

function Workspace({ viewer }: { viewer: Viewer }) {
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void startApp(viewer);
  }, [viewer]);
  return <MainLayout />;
}

function isLoginRoute(): boolean {
  return /\/login-v2\/?$/.test(window.location.pathname);
}

export default function App() {
  const loginRoute = isLoginRoute();
  const viewer = useQuery({ queryKey: ['viewer'], queryFn: fetchViewer, enabled: !loginRoute, staleTime: Infinity, retry: false });
  // Unreachable /api/version keeps the workbook UI (the behaviour before Riviu Data).
  const version = useQuery({ queryKey: ['version'], queryFn: fetchVersion, enabled: !loginRoute, staleTime: Infinity, retry: 2 });
  const storage = version.data?.storage ?? (version.isError ? 'workbook' : undefined);
  useEffect(() => {
    // Riviu Data has a public viewer at /; the workbook UI still needs a login.
    if (storage === 'workbook' && viewer.data && !viewer.data.authenticated) window.location.assign('/login');
  }, [viewer.data, storage]);
  if (loginRoute) return <LoginPage />;
  if (viewer.data && storage === 'postgres') {
    return <RiviuDataApp viewer={viewer.data} adminPage={isAdminPage(window.location.pathname, viewer.data)} />;
  }
  if (!viewer.data || !storage || !viewer.data.authenticated) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-muted-foreground">
        <Loader2Icon className="size-4 animate-spin" /> Đang tải...
      </div>
    );
  }
  return <Workspace viewer={viewer.data} />;
}
