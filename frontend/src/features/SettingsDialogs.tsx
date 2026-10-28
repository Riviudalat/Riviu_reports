import { useEffect, useId, useRef, useState } from 'react';
import { CheckCircle2Icon, CookieIcon, ExternalLinkIcon, Loader2Icon, NetworkIcon, SaveIcon, Trash2Icon, UploadIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Badge, Textarea } from '@/components/ui/misc';
import { PlatformIcon } from '@/components/common';
import { errorMessage, getJson, postJson } from '@/lib/api';
import { formatCookieCheckedAt } from '@/lib/format';
import { cn } from '@/lib/utils';
import {
  appStore, closeModal, cookieAction, openModal, COOKIE_IMPORT_MAX_BYTES, notify, refreshCookieStatus, refreshProxyStatus, submitGooglePaste, uiIsBusy, useApp, type CookieKind,
} from '@/state/app';

// ---- Proxy ----------------------------------------------------------------------------

interface ProxyTestResult {
  build?: string;
  count?: number;
  okCount?: number;
  uniqueIps?: unknown;
  message?: string;
  results?: Array<{ ok?: boolean; ip?: string; location?: string; name?: string; label?: string; line?: number }>;
}

function isLegacyProxyTestResponse(data: ProxyTestResult): boolean {
  if (!data || typeof data !== 'object') return true;
  if (String(data.build || '').trim()) return false;
  if (data.uniqueIps != null) return true;
  if (String(data.message || '').includes('IP khác')) return true;
  const total = Number(data.count || 0);
  const rows = Array.isArray(data.results) ? data.results.length : 0;
  if (total > 0 && rows > total) return true;
  return data.okCount == null && rows > 0;
}

function proxyResultName(item: NonNullable<ProxyTestResult['results']>[number], index: number): string {
  if (item?.name) return item.name;
  const label = String(item?.label || '');
  const region = label.match(/region-([A-Za-z0-9_-]+)/i);
  if (region) return region[1].toUpperCase();
  const user = label.match(/^HTTP ([^@]+)@/);
  if (user) return user[1].length <= 28 ? user[1] : `${user[1].slice(0, 25)}...`;
  return `Dòng ${item?.line || index + 1}`;
}

export function proxyTestLines(data: ProxyTestResult): { ok: boolean; text: string } {
  if (isLegacyProxyTestResponse(data)) return { ok: false, text: 'Server đang chạy code cũ (chưa khởi động lại sau cập nhật). Khởi động lại server rồi test proxy lại.' };
  const results = Array.isArray(data.results) ? data.results : [];
  const total = Number(data.count || results.length || 0);
  const okCount = Number(data.okCount ?? results.filter(item => item && item.ok && item.ip).length);
  const lines = [`${okCount}/${total} proxy OK`];
  results.forEach((item, index) => lines.push(`${proxyResultName(item, index)} → ${item && item.ok && item.ip ? item.ip : 'lỗi'}`));
  return { ok: okCount > 0, text: lines.join('\n') };
}

export function ProxyDialog() {
  const open = useApp(state => state.modal === 'proxy');
  return (
    <Dialog open={open} onOpenChange={value => !value && closeModal()}>
      <DialogContent className="max-w-4xl">{open && <ProxyBody />}</DialogContent>
    </Dialog>
  );
}

type ProxyRow = NonNullable<ProxyTestResult['results']>[number];

function ProxyBody() {
  const [text, setText] = useState(() => appStore.get().proxyText);
  const [count, setCount] = useState(() => appStore.get().proxyCount);
  const [result, setResult] = useState<{ tone: '' | 'ok' | 'error'; text: string; rows: ProxyRow[] }>({ tone: '', text: '', rows: [] });
  const [busy, setBusy] = useState<'' | 'save' | 'test'>('');
  const textareaId = useId();
  useEffect(() => {
    let alive = true;
    void getJson<{ text?: string; count?: number }>('/proxy-list').then(({ data }) => {
      if (!alive) return;
      setCount(Number(data.count || 0));
      setText(current => (current.trim() ? current : String(data.text || '')));
    }).catch(() => undefined);
    return () => { alive = false; };
  }, []);

  const save = async () => {
    setBusy('save');
    try {
      const { response, data } = await postJson<{ count?: number; message?: string; detail?: string }>('/proxy-list', { text });
      if (!response.ok) throw new Error(data.detail || data.message || 'Lưu thất bại');
      setCount(Number(data.count || 0));
      await refreshProxyStatus();
      notify(data.message || 'Đã lưu proxy', 'success');
    } catch (error) {
      notify(errorMessage(error, 'Không lưu được proxy'), 'error');
    } finally {
      setBusy('');
    }
  };

  const test = async () => {
    setBusy('test');
    setResult({ tone: '', text: 'Đang kiểm tra proxy...', rows: [] });
    try {
      const { response, data } = await postJson<ProxyTestResult & { detail?: string }>(`/proxy-test?_=${Date.now()}`, { text, save: false });
      if (!response.ok) throw new Error(data.detail || data.message || 'Test thất bại');
      const lines = proxyTestLines(data);
      const legacy = isLegacyProxyTestResponse(data);
      setResult({ tone: lines.ok ? 'ok' : 'error', text: legacy ? lines.text : lines.text.split('\n')[0], rows: legacy ? [] : data.results || [] });
      notify(data.message || 'Test xong', Number(data.okCount || 0) > 0 ? 'success' : 'warn');
    } catch (error) {
      setResult({ tone: 'error', text: errorMessage(error, 'Test thất bại'), rows: [] });
      notify(errorMessage(error, 'Test proxy thất bại'), 'error');
    } finally {
      setBusy('');
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle><NetworkIcon className="size-4 text-primary" /> Cấu hình proxy</DialogTitle>
        <DialogDescription>
          {count > 0 ? `Đã lưu ${count} proxy · mỗi dòng 1 proxy.` : 'Chưa có proxy. Dán vào ô bên dưới rồi Test hoặc Lưu.'}
        </DialogDescription>
      </DialogHeader>
      <DialogBody className="grid gap-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <div className="flex min-w-0 flex-col gap-1.5">
          <label htmlFor={textareaId} className="text-xs font-medium text-muted-foreground">Danh sách proxy</label>
          <Textarea id={textareaId} value={text} onChange={event => setText(event.target.value)} spellCheck={false} autoComplete="off" wrap="off"
            className="h-72 resize-none text-xs leading-5"
            placeholder={'Mỗi dòng 1 proxy\n103.162.31.100:8080:user:pass\nuser:pass@1.2.3.4:8080\nhttp://user:pass@host:port'} />
          <p className="text-[11px] text-muted-foreground" title="host:port:user:pass · user:pass@host:port · http://user:pass@host:port · socks5://... · host:port">
            Dạng: <code>host:port:user:pass</code>, <code>user:pass@host:port</code>, <code>http://</code>, <code>socks5://</code>
          </p>
        </div>
        <div className="flex min-w-0 flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-medium text-muted-foreground">Kết quả test</span>
            {result.text && (
              <span className={cn('text-xs font-semibold', result.tone === 'ok' && 'text-success', result.tone === 'error' && 'text-destructive')} role="status">{result.text}</span>
            )}
          </div>
          <div className="h-72 overflow-y-auto rounded-md border bg-muted/30" data-testid="proxy-result">
            {result.rows.length === 0 ? (
              <p className="flex h-full items-center justify-center px-4 text-center text-sm text-muted-foreground">
                {busy === 'test' ? <Loader2Icon className="size-5 animate-spin" /> : 'Bấm Test proxy để xem IP và khu vực của từng proxy.'}
              </p>
            ) : (
              <ul className="divide-y text-xs">
                {result.rows.map((item, index) => {
                  const ok = Boolean(item?.ok && item.ip);
                  return (
                    <li key={item?.line ?? index} className="flex items-center gap-2 px-3 py-1.5">
                      <span className={cn('size-2 shrink-0 rounded-full', ok ? 'bg-success' : 'bg-destructive')} aria-hidden="true" />
                      <span className="w-6 shrink-0 tabular-nums text-muted-foreground">{item?.line || index + 1}</span>
                      <span className="w-28 shrink-0 font-mono tabular-nums">{ok ? item.ip : 'Lỗi'}</span>
                      <span className={cn('min-w-0 flex-1 truncate', ok ? 'text-foreground/80' : 'text-destructive')}>
                        {ok ? (item.location || 'Không rõ khu vực') : 'Không kết nối được'}
                      </span>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" onClick={closeModal}>Đóng</Button>
        <Button variant="soft" disabled={Boolean(busy)} onClick={() => void save()}>{busy === 'save' ? <Loader2Icon className="animate-spin" /> : <SaveIcon />} Lưu</Button>
        <Button disabled={Boolean(busy)} onClick={() => void test()}>{busy === 'test' ? <Loader2Icon className="animate-spin" /> : <NetworkIcon />} {busy === 'test' ? 'Đang test...' : 'Test proxy'}</Button>
      </DialogFooter>
    </>
  );
}

// ---- Cookie sessions ------------------------------------------------------------------

const COOKIE_LABELS: Record<string, string> = {
  none: 'Chưa import', unchecked: 'Chưa kiểm tra', valid: 'Hợp lệ', expired: 'Hết hạn', invalid: 'Không hợp lệ', checkpoint: 'Cần xác minh', unknown: 'Chưa xác định', storage_unavailable: 'Kho bảo mật không khả dụng',
};

export function CookieDialog({ kind }: { kind: CookieKind }) {
  const open = useApp(state => state.modal === (kind === 'threads' ? 'threadsCookie' : 'tiktokCookie'));
  const busy = useApp(state => state.cookieBusy[kind]);
  return (
    <Dialog open={open} onOpenChange={value => { if (!value && !busy) closeModal(); }}>
      <DialogContent className="max-w-xl" closeDisabled={busy} onEscapeKeyDown={event => { if (busy) event.preventDefault(); }} onPointerDownOutside={event => { if (busy) event.preventDefault(); }}>
        {open && <CookieBody kind={kind} />}
      </DialogContent>
    </Dialog>
  );
}

/** TikTok and Threads cookies in one dialog with a tab per platform (the V2 settings menu). */
export function CookiesDialog() {
  const modal = useApp(state => state.modal);
  const open = modal === 'threadsCookie' || modal === 'tiktokCookie';
  const kind: CookieKind = modal === 'threadsCookie' ? 'threads' : 'tiktok';
  const busy = useApp(state => state.cookieBusy.threads || state.cookieBusy.tiktok);
  return (
    <Dialog open={open} onOpenChange={value => { if (!value && !busy) closeModal(); }}>
      <DialogContent className="max-w-xl" closeDisabled={busy} onEscapeKeyDown={event => { if (busy) event.preventDefault(); }} onPointerDownOutside={event => { if (busy) event.preventDefault(); }}>
        {open && <CookieBody key={kind} kind={kind} tabs />}
      </DialogContent>
    </Dialog>
  );
}

function CookieTabs({ kind }: { kind: CookieKind }) {
  const busy = useApp(state => state.cookieBusy.threads || state.cookieBusy.tiktok);
  return (
    <div className="mt-2 inline-flex w-fit rounded-lg bg-muted p-1" role="tablist" aria-label="Cookie nền tảng">
      {(['tiktok', 'threads'] as const).map(value => (
        <button key={value} type="button" role="tab" aria-selected={kind === value} disabled={busy && kind !== value}
          onClick={() => openModal(value === 'threads' ? 'threadsCookie' : 'tiktokCookie')}
          className={cn('flex items-center gap-1.5 rounded-md px-3 py-1 text-sm font-medium disabled:opacity-50', kind === value ? 'bg-card shadow-sm' : 'text-muted-foreground hover:text-foreground')}>
          <PlatformIcon platform={value} /> {value === 'threads' ? 'Threads' : 'TikTok'}
        </button>
      ))}
    </div>
  );
}

function CookieBody({ kind, tabs = false }: { kind: CookieKind; tabs?: boolean }) {
  const status = useApp(state => state.cookies[kind]);
  const busy = useApp(state => uiIsBusy(state));
  const ownBusy = useApp(state => state.cookieBusy[kind]);
  const [source, setSource] = useState<'file' | 'paste'>('file');
  const [file, setFile] = useState<File | null>(null);
  const [pasted, setPasted] = useState('');
  const [feedback, setFeedback] = useState('');
  const [confirmDelete, setConfirmDelete] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);
  const platform = kind === 'threads' ? 'threads' : 'tiktok';
  const label = kind === 'threads' ? 'Threads' : 'TikTok';
  useEffect(() => { void refreshCookieStatus(kind); }, [kind]);
  useEffect(() => { if (!status.configured) setConfirmDelete(false); }, [status.configured]);

  const clearInputs = () => {
    setFile(null);
    setPasted('');
    if (fileInput.current) fileInput.current.value = '';
  };
  const switchSource = (next: 'file' | 'paste') => {
    if (ownBusy || next === source) return;
    clearInputs();
    setSource(next);
  };
  const run = async (action: 'import' | 'verify' | 'delete') => {
    let body = '';
    setFeedback(action === 'import' ? 'Đang đọc và kiểm tra cookie...' : `Đang xử lý phiên ${label}...`);
    if (action === 'import') {
      if (source === 'file') {
        if (!file) { setFeedback('Chọn file JSON trước khi import.'); return; }
        if (file.size > COOKIE_IMPORT_MAX_BYTES) { setFeedback('File cookie quá lớn, tối đa 256 KiB.'); return; }
        body = await file.text();
      } else {
        body = pasted;
      }
    }
    try {
      setFeedback(await cookieAction(kind, action, body));
    } finally {
      clearInputs();
      setConfirmDelete(false);
    }
  };
  const stateLabel = COOKIE_LABELS[status.state] || 'Chưa xác định';
  const tone = status.state === 'valid' ? 'success' : ['expired', 'checkpoint'].includes(status.state) ? 'warning' : ['invalid', 'storage_unavailable'].includes(status.state) ? 'destructive' : 'secondary';
  const meta = status.configured
    ? `${status.count || 0} cookie${status.checkedAt ? ` • ${formatCookieCheckedAt(status.checkedAt)}` : ' • Chưa kiểm tra'}`
    : 'Chưa có cookie để sử dụng.';
  const showMessage = !['valid', 'none', 'unchecked'].includes(status.state) && status.message;
  return (
    <>
      <DialogHeader>
        {tabs ? <DialogTitle><CookieIcon className="size-4 text-primary" /> Cookie đăng nhập</DialogTitle> : <DialogTitle><PlatformIcon platform={platform} /> Cookie {label}</DialogTitle>}
        <DialogDescription>
          {kind === 'tiktok' ? 'Cho bài chỉ xem được khi đăng nhập. Dùng tuyến proxy/direct đang chọn khi quét.' : 'Dùng tuyến proxy/direct đang chọn khi quét.'}
        </DialogDescription>
        {tabs && <CookieTabs kind={kind} />}
      </DialogHeader>
      <DialogBody className="flex flex-col gap-4">
        {kind === 'tiktok' && (
          <p className="rounded-lg border border-warning/40 bg-warning/10 p-3 text-sm" role="note">
            <b>Dùng tài khoản TikTok phụ.</b> Quét bằng cookie tài khoản có thể khiến TikTok giới hạn hoặc yêu cầu xác minh tài khoản đó.
          </p>
        )}
        <section className="rounded-lg border p-3">
          <div className="flex items-center justify-between gap-2">
            <h3 className="text-sm font-semibold">Phiên đã lưu</h3>
            <Badge variant={tone} data-testid={`${kind}-cookie-state`}>{stateLabel}</Badge>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">{meta}</p>
          {showMessage && <p className="mt-1 text-xs text-muted-foreground">{status.message}</p>}
          {!confirmDelete ? (
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="soft" size="sm" disabled={busy || !status.configured} onClick={() => void run('verify')}><CheckCircle2Icon /> Kiểm tra lại</Button>
              <Button variant="ghost" size="sm" className="text-destructive" disabled={busy || !status.configured} onClick={() => setConfirmDelete(true)}><Trash2Icon /> Xóa phiên</Button>
            </div>
          ) : (
            <div className="mt-3 flex flex-wrap items-center gap-2 rounded-md bg-destructive/10 p-2 text-sm">
              <span>Xóa cookie đã lưu khỏi máy chủ?</span>
              <Button variant="outline" size="sm" disabled={ownBusy} onClick={() => setConfirmDelete(false)} autoFocus>Giữ lại</Button>
              <Button variant="destructive" size="sm" disabled={busy} onClick={() => void run('delete')}>Xóa phiên</Button>
            </div>
          )}
        </section>
        <section className="flex flex-col gap-2">
          <h3 className="text-sm font-semibold">Import phiên mới</h3>
          <div className="inline-flex w-fit rounded-lg bg-muted p-1" role="tablist" aria-label="Nguồn cookie">
            {(['file', 'paste'] as const).map(value => (
              <button key={value} type="button" role="tab" aria-selected={source === value} disabled={ownBusy} onClick={() => switchSource(value)}
                className={cn('rounded-md px-3 py-1 text-sm', source === value ? 'bg-card shadow-sm' : 'text-muted-foreground')}>
                {value === 'file' ? 'Chọn file JSON' : 'Dán JSON'}
              </button>
            ))}
          </div>
          {source === 'file' ? (
            <div role="tabpanel">
              <button type="button" disabled={busy} onClick={() => fileInput.current?.click()}
                className="flex w-full flex-col items-center gap-1 rounded-lg border-2 border-dashed px-4 py-5 text-sm transition hover:border-primary/60 hover:bg-accent/40 disabled:opacity-50">
                <UploadIcon className="size-5 text-primary" />
                <strong>Chọn file cookie .json</strong>
                <span className="text-xs text-muted-foreground">Tối đa 256 KiB</span>
              </button>
              <input ref={fileInput} type="file" className="sr-only" accept=".json,application/json" aria-label={`Chọn file cookie ${label} JSON`} onChange={event => setFile(event.target.files?.[0] || null)} />
              {file && <p className="mt-1 text-xs text-muted-foreground" role="status">{file.name} · {(file.size / 1024).toFixed(1)} KiB</p>}
            </div>
          ) : (
            <div role="tabpanel">
              <Textarea rows={5} value={pasted} disabled={busy} onChange={event => setPasted(event.target.value)} autoComplete="off" spellCheck={false}
                aria-label="Dán nguyên mảng JSON đã export" placeholder={`[{"domain":".${kind === 'tiktok' ? 'tiktok' : 'threads'}.com", "name":"sessionid", ...}]`} />
            </div>
          )}
        </section>
        {feedback && <p className="rounded-md bg-muted/60 px-3 py-2 text-sm" role="status" aria-live="polite">{feedback}</p>}
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" disabled={ownBusy} onClick={closeModal}>Đóng</Button>
        <Button disabled={busy} onClick={() => void run('import')}>{ownBusy ? <Loader2Icon className="animate-spin" /> : <UploadIcon />} Import &amp; kiểm tra</Button>
      </DialogFooter>
    </>
  );
}

// ---- Google paste-back login ------------------------------------------------------------

export function GooglePasteDialog() {
  const open = useApp(state => state.modal === 'googlePaste');
  const authUrl = useApp(state => state.googlePasteUrl);
  const [value, setValue] = useState('');
  const [result, setResult] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => { if (open) { setValue(''); setResult(''); } }, [open]);
  const submit = async () => {
    if (!value.trim()) { setResult('Hãy dán địa chỉ localhost mà Google chuyển tới.'); return; }
    setBusy(true);
    setResult('Đang xác nhận...');
    try { setResult(await submitGooglePaste(value)); } finally { setBusy(false); }
  };
  return (
    <Dialog open={open} onOpenChange={next => !next && closeModal()}>
      <DialogContent className="max-w-xl">
        <DialogHeader>
          <DialogTitle>Đăng nhập Google</DialogTitle>
          <DialogDescription>Ứng dụng chạy trên server nên không tự mở được trình duyệt.</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3 text-sm">
          <ol className="list-decimal space-y-1.5 pl-5">
            <li><a href={authUrl} target="_blank" rel="noopener noreferrer" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">Mở trang đăng nhập Google <ExternalLinkIcon className="size-3.5" /></a> và cho phép quyền truy cập.</li>
            <li>Trình duyệt sẽ chuyển tới một địa chỉ <code>http://localhost…</code> và báo không tải được trang. Đó là bình thường.</li>
            <li>Sao chép toàn bộ địa chỉ đó trên thanh địa chỉ và dán vào ô dưới đây.</li>
          </ol>
          <Textarea rows={3} value={value} onChange={event => setValue(event.target.value)} placeholder="http://localhost:…/?state=…&code=…" aria-label="Địa chỉ localhost" />
          {result && <p className="text-sm text-muted-foreground" role="status">{result}</p>}
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={closeModal}>Đóng</Button>
          <Button disabled={busy} onClick={() => void submit()}>{busy && <Loader2Icon className="animate-spin" />} Xác nhận</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
