import { useEffect, useState } from 'react';
import { MonitorIcon, MoonIcon, SunIcon } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu';

type Theme = 'light' | 'dark' | 'system';
// v2: the old key held 'system' for everyone (it was saved on load), so light could not become the default.
const THEME_KEY = 'riviuTheme.v2';

function readTheme(): Theme {
  try {
    const value = localStorage.getItem(THEME_KEY);
    // Light unless the visitor picked dark or "theo hệ thống" (owner's default since 2026-11).
    return value === 'dark' || value === 'system' ? value : 'light';
  } catch {
    return 'light';
  }
}

function applyTheme(theme: Theme) {
  const dark = theme === 'dark' || (theme === 'system' && window.matchMedia?.('(prefers-color-scheme: dark)').matches);
  document.documentElement.classList.toggle('dark', Boolean(dark));
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(readTheme);
  useEffect(() => {
    applyTheme(theme);
    try { localStorage.setItem(THEME_KEY, theme); } catch { /* storage unavailable */ }
    if (theme !== 'system' || !window.matchMedia) return;
    const media = window.matchMedia('(prefers-color-scheme: dark)');
    const listener = () => applyTheme('system');
    media.addEventListener('change', listener);
    return () => media.removeEventListener('change', listener);
  }, [theme]);
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label="Giao diện sáng/tối" title="Giao diện sáng/tối">
          <SunIcon className="dark:hidden" />
          <MoonIcon className="hidden dark:block" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="min-w-36">
        <DropdownMenuRadioGroup value={theme} onValueChange={value => setTheme(value as Theme)}>
          <DropdownMenuRadioItem value="light"><SunIcon className="size-4" /> Sáng</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="dark"><MoonIcon className="size-4" /> Tối</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="system"><MonitorIcon className="size-4" /> Theo hệ thống</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
