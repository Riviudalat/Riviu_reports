import { useEffect, useId } from 'react';
import { LogInIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card, Input, Label } from '@/components/ui/misc';
import { ThemeToggle } from '@/components/theme';

/**
 * React login screen (served at /login-v2 until the backend switches over). It posts the
 * `username` + `password` form fields to /login as a normal form submission, so the browser
 * sends its Origin header (the backend's login CSRF check) and follows the redirect.
 */
export function LoginPage() {
  const userId = useId();
  const passId = useId();
  const error = new URLSearchParams(window.location.search).get('error') || '';
  useEffect(() => {
    // A link from another site arrives without the SameSite=Strict cookie; this same-origin
    // probe carries it, so a logged-in user goes straight back in.
    fetch('/auth-status', { credentials: 'same-origin' })
      .then(response => (response.ok ? response.json() : null))
      .then(data => { if (data && data.authenticated) window.location.replace('/'); })
      .catch(() => undefined);
  }, []);
  return (
    <div className="relative flex min-h-full items-center justify-center bg-gradient-to-br from-accent/60 via-background to-background p-4">
      <div className="absolute right-4 top-4"><ThemeToggle /></div>
      <Card className="w-full max-w-sm p-7 shadow-lg">
        <div className="mb-6 flex items-center gap-2.5">
          <img src={`${import.meta.env.BASE_URL}logo.png`} alt="Riviu" className="h-8 w-auto" />
          <span className="text-xl font-semibold tracking-tight">Reports</span>
        </div>
        <h1 className="mb-1 text-lg font-semibold">Đăng nhập</h1>
        <p className="mb-5 text-sm text-muted-foreground">Nhập tài khoản được cấp để tiếp tục.</p>
        {error && <p className="mb-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive" role="alert">{error}</p>}
        <form method="post" action="/login" autoComplete="on" className="flex flex-col gap-4">
          <div className="flex flex-col gap-2">
            <Label htmlFor={userId}>Tên đăng nhập</Label>
            <Input id={userId} name="username" required autoFocus autoComplete="username" maxLength={128} />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor={passId}>Mật khẩu</Label>
            <Input id={passId} name="password" type="password" required autoComplete="current-password" maxLength={1024} />
          </div>
          <Button type="submit" size="lg" className="mt-1 w-full"><LogInIcon /> Đăng nhập</Button>
        </form>
      </Card>
    </div>
  );
}
