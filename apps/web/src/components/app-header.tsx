"use client";

import { MonitorIcon, MoonIcon, PlusIcon, SearchCheckIcon, SettingsIcon, SunIcon } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTheme } from "next-themes";
import { useSyncExternalStore } from "react";

import { Button } from "@/components/ui/button";
import { AccountMenu, WorkspaceSwitcher } from "@/components/workspace-switcher";
import { cn } from "@/lib/utils";

const AUTH_PAGES = ["/sign-in", "/sign-up"];

const NAV = [
  { href: "/", label: "Projects", match: (path: string) => path === "/" || path.startsWith("/projects") },
  { href: "/settings", label: "Settings", match: (path: string) => path.startsWith("/settings") },
];

const subscribe = () => () => {};

function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  // The theme is only known in the browser; render a stable placeholder on the server.
  const mounted = useSyncExternalStore(subscribe, () => true, () => false);
  const next = theme === "light" ? "dark" : theme === "dark" ? "system" : "light";
  const Icon = !mounted ? MonitorIcon : theme === "light" ? SunIcon : theme === "dark" ? MoonIcon : MonitorIcon;
  return (
    <Button
      variant="ghost"
      size="icon"
      onClick={() => setTheme(next)}
      aria-label={mounted ? `Theme: ${theme}. Switch to ${next}.` : "Switch theme"}
      title={mounted ? `Theme: ${theme}` : undefined}
    >
      <Icon />
    </Button>
  );
}

export function AppHeader() {
  const pathname = usePathname();
  const onAuthPage = AUTH_PAGES.includes(pathname);
  return (
    <header className="sticky top-0 z-40 border-b bg-background/85 backdrop-blur supports-[backdrop-filter]:bg-background/70">
      <div className="mx-auto flex h-14 max-w-6xl items-center gap-4 px-4 sm:px-6">
        <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
          <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
            <SearchCheckIcon className="size-4" />
          </span>
          <span>Anvesha</span>
        </Link>
        {!onAuthPage && <WorkspaceSwitcher />}
        <nav aria-label="Main" className={cn("flex items-center gap-1", onAuthPage && "hidden")}>
          {NAV.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              aria-current={item.match(pathname) ? "page" : undefined}
              className={cn(
                "rounded-md px-3 py-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground",
                item.match(pathname) && "bg-muted font-medium text-foreground",
              )}
            >
              {item.href === "/settings" ? (
                <>
                  <SettingsIcon className="size-4 sm:hidden" aria-hidden />
                  <span className="max-sm:sr-only">{item.label}</span>
                </>
              ) : (
                item.label
              )}
            </Link>
          ))}
        </nav>
        <div className="ml-auto flex items-center gap-1">
          <ThemeToggle />
          {!onAuthPage && (
            <>
              <Button asChild size="sm">
                <Link href="/new">
                  <PlusIcon />
                  <span className="max-sm:sr-only">New research</span>
                </Link>
              </Button>
              <AccountMenu />
            </>
          )}
        </div>
      </div>
    </header>
  );
}
