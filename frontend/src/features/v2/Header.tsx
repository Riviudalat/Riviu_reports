import {
  CalendarClockIcon, CookieIcon, HistoryIcon, LayoutDashboardIcon, LogInIcon, LogOutIcon, MonitorCogIcon, NetworkIcon, Settings2Icon, ShieldCheckIcon, UserIcon,
} from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ThemeToggle } from '@/components/theme';
import { PlatformIcon } from '@/components/common';
import { ROLE_LABELS } from '@/lib/auth';
import { PLATFORMS, PLATFORM_KEYS } from '@/lib/platforms';
import { cn } from '@/lib/utils';
import { canAdmin, openModal, useApp } from '@/state/app';
import { selectRdPlatform } from '@/state/riviuData';

export function Brand() {
  return (
    <a href="/" className="flex shrink-0 flex-col items-start gap-0.5 border-l-[3px] border-primary pl-2.5 leading-none" aria-label="Riviu Reports — trang báo cáo">
      <img src={`${import.meta.env.BASE_URL}logo.png`} alt="Riviu" className="h-6 w-auto sm:h-7" />
      <span className="text-[13px] font-extrabold tracking-tight text-foreground sm:text-sm">Reports</span>
    </a>
  );
}

export function PlatformSwitch({ className }: { className?: string }) {
  const active = useApp(state => state.activePlatform);
  return (
    <div className={cn('inline-flex rounded-lg bg-muted p-1', className)} role="group" aria-label="Nền tảng báo cáo">
      {PLATFORM_KEYS.map(key => {
        const selected = key === active;
        return (
          <button
            key={key}
            type="button"
            aria-pressed={selected}
            onClick={() => void selectRdPlatform(key)}
            className={cn(
              'flex h-8 min-w-0 flex-1 items-center justify-center gap-2 rounded-md px-4 text-sm font-semibold transition md:min-w-28 md:flex-none',
              selected ? 'bg-card text-foreground shadow-sm ring-1 ring-border/70' : 'text-muted-foreground hover:bg-card/60 hover:text-foreground',
            )}
          >
            <PlatformIcon platform={key} />
            {PLATFORMS[key].label}
          </button>
        );
      })}
    </div>
  );
}

/** POST /logout (the server checks Origin and redirects to `/`). */
export function logout() {
  const form = document.createElement('form');
  form.method = 'post';
  form.action = '/logout';
  document.body.appendChild(form);
  form.submit();
}

function LogoutItem() {
  return <DropdownMenuItem onSelect={logout} data-testid="logout"><LogOutIcon /> Đăng xuất</DropdownMenuItem>;
}

function AccountMenu() {
  const viewer = useApp(state => state.viewer);
  const admin = useApp(state => canAdmin(state));
  const name = viewer.username || (admin ? 'Quản trị' : 'Tài khoản');
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="max-w-44 gap-1.5 px-2" aria-label={`Tài khoản: ${name}`} data-testid="account-menu">
          <span className={cn('flex size-6 shrink-0 items-center justify-center rounded-full', admin ? 'bg-accent text-accent-foreground' : 'bg-muted text-muted-foreground')}>
            {admin ? <ShieldCheckIcon className="size-3.5" /> : <UserIcon className="size-3.5" />}
          </span>
          <span className="hidden truncate xl:inline">{name}</span>
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-52">
        <DropdownMenuLabel className="flex flex-col gap-0.5">
          <span className="truncate text-sm font-semibold text-foreground" data-testid="account-name">{name}</span>
          <span>{ROLE_LABELS[viewer.role]}</span>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <LogoutItem />
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

function AdminActions({ scheduleAvailable }: { scheduleAvailable: boolean }) {
  const serverMode = useApp(state => state.viewer.serverMode);
  return (
    <>
      {scheduleAvailable && (
        <Button variant="ghost" size="sm" onClick={() => openModal('schedule')} aria-label="Lịch tự động" title="Lịch tự động">
          <CalendarClockIcon /><span className="hidden lg:inline">Lịch tự động</span>
        </Button>
      )}
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" size="sm" aria-label="Cài đặt" title="Cài đặt" data-testid="settings-menu">
            <Settings2Icon /><span className="hidden lg:inline">Cài đặt</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="min-w-56">
          <DropdownMenuLabel>Cấu hình quét</DropdownMenuLabel>
          <DropdownMenuItem onSelect={() => openModal('proxy')}><NetworkIcon /> Proxy xoay</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openModal('threadsCookie')}><CookieIcon /> Cookie Threads</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => openModal('tiktokCookie')}><CookieIcon /> Cookie TikTok</DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => openModal('history')}><HistoryIcon /> Lịch sử quét</DropdownMenuItem>
          {serverMode && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild><a href="/"><LayoutDashboardIcon /> Trang xem báo cáo</a></DropdownMenuItem>
              <DropdownMenuItem asChild><a href="/classic"><MonitorCogIcon /> Giao diện cũ</a></DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

function ViewerActions() {
  const viewer = useApp(state => state.viewer);
  const admin = useApp(state => canAdmin(state));
  return (
    <>
      <Button variant="ghost" size="sm" onClick={() => openModal('history')} aria-label="Lịch sử quét" title="Lịch sử quét">
        <HistoryIcon /><span className="hidden sm:inline">Lịch sử quét</span>
      </Button>
      <ThemeToggle />
      {viewer.serverMode && admin && (
        <Button variant="outline" size="sm" asChild>
          <a href="/admin" data-testid="admin-link"><ShieldCheckIcon /><span className="hidden sm:inline">Quản trị</span></a>
        </Button>
      )}
      {viewer.serverMode && viewer.authenticated && !admin && <AccountMenu />}
      {viewer.serverMode && !viewer.authenticated && (
        <Button variant="outline" size="sm" asChild>
          <a href="/admin" title="Đăng nhập quản trị" data-testid="login-link"><LogInIcon /> Đăng nhập</a>
        </Button>
      )}
    </>
  );
}

export function Header({ adminPage, scheduleAvailable }: { adminPage: boolean; scheduleAvailable: boolean }) {
  const serverMode = useApp(state => state.viewer.serverMode);
  return (
    <header className="sticky top-0 z-30 border-b bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/85">
      <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-5 gap-y-2.5 px-4 py-2.5 sm:px-6">
        <Brand />
        <PlatformSwitch className="order-last w-full md:order-none md:w-auto" />
        <nav className="ml-auto flex min-w-0 items-center gap-1" aria-label={adminPage ? 'Quản trị' : 'Liên kết'}>
          {adminPage ? (
            <>
              <AdminActions scheduleAvailable={scheduleAvailable} />
              <ThemeToggle />
              {serverMode && <AccountMenu />}
            </>
          ) : <ViewerActions />}
        </nav>
      </div>
    </header>
  );
}
