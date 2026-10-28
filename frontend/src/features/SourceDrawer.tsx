import { useId, useRef, useState } from 'react';
import { CloudUploadIcon, CopyIcon, DownloadIcon, FilePlus2Icon, FolderOpenIcon, KeyRoundIcon, Link2Icon, Loader2Icon, LogInIcon, RefreshCwIcon, ShieldCheckIcon, Trash2Icon } from 'lucide-react';
import { toast } from 'sonner';
import { Dialog, DialogTitle, SheetContent } from '@/components/ui/dialog';
import { Button } from '@/components/ui/button';
import { Input, NativeSelect } from '@/components/ui/misc';
import { AdminOnly, PlatformIcon, useIsAdmin } from '@/components/common';
import { copyText } from '@/lib/format';
import { PLATFORMS, PLATFORM_KEYS, type PlatformKey } from '@/lib/platforms';
import { dataSheetsVisibleFirst, sheetOptionLabel } from '@/lib/sheets';
import { cn } from '@/lib/utils';
import {
  activeSource, connectGoogleOAuth, downloadCurrentWorkbook, googlePushEnabled, pushToGoogle, removeGoogleServiceAccount, selectFile, setSourceDrawer,
  setSourceRowSheet, setSourceUrl, syncGoogleSheet, uiIsBusy, updateFileList, uploadFile, uploadGoogleOauthClient, uploadGoogleServiceAccount, useApp,
} from '@/state/app';

function Block({ icon, title, children }: { icon: React.ReactNode; title: string; children: React.ReactNode }) {
  return (
    <section className="rounded-xl border bg-card p-4">
      <div className="mb-3 flex items-center gap-2.5">
        <span className="flex size-7 items-center justify-center rounded-md bg-accent text-accent-foreground [&_svg]:size-4">{icon}</span>
        <h3 className="text-sm font-semibold">{title}</h3>
      </div>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  );
}

function PlatformSourceRow({ platform }: { platform: PlatformKey }) {
  const admin = useIsAdmin();
  const source = useApp(state => state.sources[platform]);
  const busy = useApp(state => uiIsBusy(state));
  const pushEnabled = useApp(state => googlePushEnabled(state, platform));
  const [syncing, setSyncing] = useState(false);
  const [pushing, setPushing] = useState(false);
  const label = PLATFORMS[platform].label;
  const sheets = dataSheetsVisibleFirst(source.sheets, source.hiddenSheets);
  const sheetValue = sheets.includes(source.pushSheet) ? source.pushSheet : (sheets.includes(source.scanSheet) ? source.scanSheet : sheets[0] || '');
  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-muted/30 p-3" data-source-platform={platform}>
      <div className="flex min-w-0 items-center justify-between gap-2 text-sm">
        <span className="flex items-center gap-2 font-medium"><PlatformIcon platform={platform} />{label}</span>
        <span className="truncate text-xs text-muted-foreground" title={source.label || source.fileId}>{source.label || source.fileId || 'Chưa chọn file'}</span>
      </div>
      {admin && (
        <>
          <div className="flex gap-2">
            <Input
              type="url"
              placeholder={`Link Google Sheet ${label}`}
              aria-label={`Link Google Sheet ${label}`}
              value={source.url}
              disabled={busy}
              onChange={event => setSourceUrl(platform, event.target.value)}
            />
            <Button
              size="sm"
              className="h-9"
              disabled={busy}
              onClick={async () => { setSyncing(true); try { await syncGoogleSheet(platform); } finally { setSyncing(false); } }}
            >
              {syncing ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
              {syncing ? 'Đang đồng bộ...' : 'Nạp sheet'}
            </Button>
          </div>
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-muted-foreground">Sheet</span>
            <NativeSelect
              className="flex-1"
              aria-label={`Sheet ${label}`}
              value={sheetValue}
              disabled={busy || !sheets.length}
              onChange={event => setSourceRowSheet(platform, event.target.value)}
            >
              {!sheets.length && <option value="">Không có sheet</option>}
              {sheets.map(sheet => <option key={sheet} value={sheet}>{sheetOptionLabel(sheet, source.hiddenSheets)}</option>)}
            </NativeSelect>
            <Button
              variant="soft"
              size="sm"
              className="h-9"
              disabled={!pushEnabled}
              title={pushEnabled ? '' : 'Cần đăng nhập Google, link đích và file/sheet của nền tảng này.'}
              onClick={async () => { setPushing(true); try { await pushToGoogle(platform); } finally { setPushing(false); } }}
            >
              {pushing ? <Loader2Icon className="animate-spin" /> : <FilePlus2Icon />}
              {pushing ? 'Đang tạo...' : 'Tạo sheet'}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}

function GoogleCredentials() {
  const status = useApp(state => state.googleStatus);
  const serviceInput = useRef<HTMLInputElement>(null);
  const oauthInput = useRef<HTMLInputElement>(null);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const account = status?.serviceAccount || {};
  const configured = Boolean(status?.configured);
  const loginLabel = status?.valid ? 'Đã đăng nhập' : status?.authorized ? 'Token hết hạn' : 'Chưa đăng nhập';
  const email = String(account.email || '').trim();
  const loginTitle = !configured
    ? 'Cần nạp file OAuth trước khi đăng nhập'
    : status?.activeCredential === 'service_account'
      ? `Đang dùng tài khoản dịch vụ ${email}; đăng nhập Google là cách thay thế.`
      : status?.accountEmail ? `Tài khoản: ${status.accountEmail}` : loginLabel;
  return (
    <Block icon={<KeyRoundIcon />} title="Google">
      <div className="flex flex-wrap gap-2">
        {!account.configured && (
          <Button variant="soft" size="sm" onClick={() => serviceInput.current?.click()} title="Khuyên dùng: tài khoản robot của Google Cloud, không cần đăng nhập Google cá nhân">
            <ShieldCheckIcon /> Nạp tài khoản dịch vụ
          </Button>
        )}
        {!configured && (
          <Button variant="outline" size="sm" onClick={() => oauthInput.current?.click()}><FilePlus2Icon /> Nạp OAuth</Button>
        )}
        <Button variant={status?.valid ? 'outline' : 'soft'} size="sm" disabled={!configured} title={loginTitle} onClick={() => void connectGoogleOAuth()} data-testid="google-login">
          <LogInIcon /> {loginLabel}
        </Button>
        <input ref={serviceInput} type="file" hidden accept=".json,application/json" onChange={event => { const file = event.target.files?.[0]; if (file) void uploadGoogleServiceAccount(file); event.target.value = ''; }} />
        <input ref={oauthInput} type="file" hidden accept=".json" onChange={event => { const file = event.target.files?.[0]; if (file) void uploadGoogleOauthClient(file); event.target.value = ''; }} />
      </div>
      {account.configured && (
        <div className="rounded-lg border border-success/30 bg-success/10 p-3 text-sm">
          <div className="flex flex-wrap items-center gap-2">
            <ShieldCheckIcon className="size-4 text-success" />
            <span className="font-medium">Tài khoản dịch vụ Google</span>
          </div>
          <code className="mt-1 block break-all text-xs">{email}</code>
          <p className="mt-2 text-xs text-muted-foreground">Chia sẻ Google Sheet cho email này với quyền <b>Người chỉnh sửa</b>.</p>
          {account.error && <p className="mt-1 text-xs text-destructive" role="status">{account.error}</p>}
          <div className="mt-2 flex flex-wrap gap-2">
            <Button variant="outline" size="sm" disabled={!email} onClick={async () => {
              try { await copyText(email); toast.success('Đã copy email tài khoản dịch vụ.'); } catch { toast.warning(`Không copy được, hãy chọn và copy: ${email}`); }
            }}><CopyIcon /> Copy</Button>
            {!confirmRemove
              ? <Button variant="ghost" size="sm" className="text-destructive" onClick={() => setConfirmRemove(true)}><Trash2Icon /> Gỡ tài khoản dịch vụ</Button>
              : (
                <span className="flex flex-wrap items-center gap-2 text-xs">
                  Gỡ tài khoản dịch vụ? Tạo sheet sẽ dùng lại đăng nhập Google (nếu có).
                  <Button variant="outline" size="sm" onClick={() => setConfirmRemove(false)}>Giữ lại</Button>
                  <Button variant="destructive" size="sm" onClick={() => { setConfirmRemove(false); void removeGoogleServiceAccount(); }}>Gỡ</Button>
                </span>
              )}
          </div>
        </div>
      )}
    </Block>
  );
}

function UploadZone() {
  const input = useRef<HTMLInputElement>(null);
  const busy = useApp(state => uiIsBusy(state));
  const [over, setOver] = useState(false);
  return (
    <Block icon={<CloudUploadIcon />} title="Nạp file Excel">
      <button
        type="button"
        disabled={busy}
        onClick={() => input.current?.click()}
        onDragOver={event => { event.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={event => { event.preventDefault(); setOver(false); const file = event.dataTransfer.files[0]; if (file) void uploadFile(file); }}
        className={cn('flex flex-col items-center gap-1.5 rounded-lg border-2 border-dashed px-4 py-6 text-sm transition hover:border-primary/60 hover:bg-accent/40 disabled:opacity-50', over && 'border-primary bg-accent/50')}
      >
        <CloudUploadIcon className="size-7 text-primary" />
        <span className="font-semibold">Kéo thả hoặc click để tải file</span>
        <span className="text-xs text-muted-foreground">Chỉ hỗ trợ .xlsx</span>
      </button>
      <input ref={input} type="file" hidden accept=".xlsx" onChange={event => { const file = event.target.files?.[0]; if (file) void uploadFile(file); event.target.value = ''; }} />
    </Block>
  );
}

function FileChooser() {
  const id = useId();
  const files = useApp(state => state.files);
  const fileId = useApp(state => activeSource(state).fileId);
  const busy = useApp(state => uiIsBusy(state));
  const platform = useApp(state => state.activePlatform);
  return (
    <Block icon={<FolderOpenIcon />} title={`File hiện có · ${PLATFORMS[platform].label}`}>
      <label htmlFor={id} className="sr-only">File của nền tảng đang chọn</label>
      <NativeSelect id={id} value={fileId} disabled={busy || !files.length} onChange={event => void selectFile(event.target.value)} data-testid="file-select">
        {!files.length && <option value="">(Không có file nào)</option>}
        {files.length > 0 && !fileId && <option value="">— Chọn file cho nền tảng này —</option>}
        {files.map(file => <option key={file.id} value={file.id}>{file.label || file.id}</option>)}
      </NativeSelect>
      <div className="flex flex-wrap gap-2">
        <Button variant="outline" size="sm" onClick={() => void updateFileList()}><RefreshCwIcon /> Làm mới danh sách</Button>
        <AdminOnly>
          <Button variant="outline" size="sm" disabled={!fileId} onClick={downloadCurrentWorkbook}><DownloadIcon /> Tải sheet</Button>
        </AdminOnly>
      </div>
    </Block>
  );
}

export function SourceDrawer() {
  const open = useApp(state => state.sourceDrawerOpen);
  const admin = useIsAdmin();
  return (
    <Dialog open={open} onOpenChange={setSourceDrawer}>
      <SheetContent className="max-w-lg">
        <div className="border-b px-5 py-4 pr-12">
          <DialogTitle>Nguồn dữ liệu</DialogTitle>
          <p className="mt-0.5 text-xs text-muted-foreground">Mỗi nền tảng có file, sheet và link Google riêng.</p>
        </div>
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto bg-muted/30 p-4">
          {admin && <GoogleCredentials />}
          <Block icon={<Link2Icon />} title={admin ? 'Nguồn Google Sheet' : 'Nguồn theo nền tảng'}>
            {PLATFORM_KEYS.map(platform => <PlatformSourceRow key={platform} platform={platform} />)}
          </Block>
          <FileChooser />
          {admin && <UploadZone />}
        </div>
      </SheetContent>
    </Dialog>
  );
}
