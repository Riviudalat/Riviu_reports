import { ArrowLeftRightIcon, CircleHelpIcon, FileSpreadsheetIcon, HistoryIcon, LogOutIcon, NotebookTabsIcon, ShieldCheckIcon, UserIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/misc';
import { ThemeToggle } from '@/components/theme';
import { ROLE_LABELS } from '@/lib/auth';
import { activeSource, openModal, setSourceDrawer, useApp } from '@/state/app';

function SourceSummary() {
  const label = useApp(state => {
    const source = activeSource(state);
    return source.label || source.fileId || '';
  });
  const sheet = useApp(state => {
    const source = activeSource(state);
    return source.scanSheet || source.displaySheet || '';
  });
  return (
    <button
      type="button"
      onClick={() => setSourceDrawer(true)}
      className="group flex min-w-0 flex-1 items-center gap-2.5 rounded-lg border border-transparent px-2 py-1 text-left transition hover:border-border hover:bg-muted/60 md:max-w-md"
      title={`${label || 'Chưa chọn file'} • ${sheet || 'Chưa chọn sheet'}`}
    >
      <span className="flex size-8 shrink-0 items-center justify-center rounded-md bg-accent text-accent-foreground">
        <FileSpreadsheetIcon className="size-4" />
      </span>
      <span className="min-w-0">
        <span data-testid="source-file" className="block truncate text-sm font-semibold">{label || 'Chưa chọn file'}</span>
        <span data-testid="source-sheet" className="block truncate text-xs text-muted-foreground">{sheet || 'Chưa chọn sheet'}</span>
      </span>
    </button>
  );
}

function UserBadge() {
  const viewer = useApp(state => state.viewer);
  if (!viewer.serverMode) return null;
  const admin = viewer.role === 'admin';
  return (
    <Badge variant={admin ? 'brand' : 'secondary'} className="h-7 gap-1.5 px-2.5" data-testid="role-badge" title={`${viewer.username || 'Người dùng'} • ${ROLE_LABELS[viewer.role]}`}>
      {admin ? <ShieldCheckIcon /> : <UserIcon />}
      <span className="max-w-28 truncate">{viewer.username || ROLE_LABELS[viewer.role]}</span>
      {viewer.username && <span className="hidden opacity-70 sm:inline">· {ROLE_LABELS[viewer.role]}</span>}
    </Badge>
  );
}

export function TopBar() {
  const serverMode = useApp(state => state.viewer.serverMode);
  return (
    <header className="sticky top-0 z-30 border-b bg-card/90 backdrop-blur supports-[backdrop-filter]:bg-card/75">
      <div className="mx-auto flex max-w-[1600px] flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
        <div className="flex shrink-0 items-center gap-2">
          <img src={`${import.meta.env.BASE_URL}logo.png`} alt="Riviu" className="h-7 w-auto" />
          <span className="text-[15px] font-semibold tracking-tight">Reports</span>
        </div>
        <div className="order-3 flex w-full min-w-0 md:order-none md:w-auto md:flex-1">
          <SourceSummary />
        </div>
        <nav className="ml-auto flex items-center gap-1.5" aria-label="Thao tác chính">
          <Button variant="outline" size="sm" onClick={() => setSourceDrawer(true)} title="Đổi nguồn dữ liệu" aria-label="Đổi nguồn">
            <ArrowLeftRightIcon /><span className="hidden lg:inline">Đổi nguồn</span>
          </Button>
          <Button variant="outline" size="sm" onClick={() => openModal('history')} title="Lịch sử quét" aria-label="Lịch sử">
            <HistoryIcon /><span className="hidden lg:inline">Lịch sử</span>
          </Button>
          <Button size="sm" onClick={() => openModal('report')} title="Đối tác và báo cáo" aria-label="Đối tác & báo cáo">
            <NotebookTabsIcon /><span className="hidden sm:inline">Đối tác & báo cáo</span>
          </Button>
          <Button variant="ghost" size="icon-sm" onClick={() => openModal('guide')} title="Hướng dẫn sử dụng" aria-label="Hướng dẫn sử dụng">
            <CircleHelpIcon />
          </Button>
          <ThemeToggle />
          <UserBadge />
          {serverMode && (
            <form method="post" action="/logout" className="contents">
              <Button variant="ghost" size="icon-sm" type="submit" title="Đăng xuất" aria-label="Đăng xuất">
                <LogOutIcon />
              </Button>
            </form>
          )}
        </nav>
      </div>
    </header>
  );
}
