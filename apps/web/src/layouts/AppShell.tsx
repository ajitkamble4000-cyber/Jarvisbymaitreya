import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useAuth } from "../hooks/useAuth";
import { useAppearanceSync } from "../hooks/useAppearanceSync";
import { useEventStream } from "../hooks/useEventStream";
import { qk, useSettingsQuery, useTaskCountsQuery } from "../services/queries";
import { browserAlertFor, showBrowserAlert } from "../services/browserNotifications";
import type { ConnectionStatus } from "../services/api";
import type { LiveEvent, TaskDetailResponse } from "../types/api";
import { useUiStore } from "../stores/ui-store";
import { Badge } from "../components/ui/Badge";
import { Button } from "../components/ui/Button";
import { Dialog } from "../components/ui/Dialog";
import { Icon } from "../components/ui/Icon";
import { IconButton } from "../components/ui/IconButton";
import { StatusDot, type StatusTone } from "../components/ui/StatusDot";
import { useToast } from "../components/ui/Toast";
import { cn } from "../components/ui/cn";
import { NAV_ITEMS, visibleNavItems, type NavItem } from "./nav";
import { SidebarCustomizeDialog } from "./SidebarCustomizeDialog";
import { CommandPalette } from "./CommandPalette";
import { WakeWordListener } from "./WakeWordListener";
import { isPaletteShortcut } from "./commandPalette";

const CONNECTION_LABEL: Record<ConnectionStatus, { tone: StatusTone; label: string }> = {
  connecting: { tone: "muted", label: "Connecting" },
  open: { tone: "success", label: "Live" },
  reconnecting: { tone: "warning", label: "Reconnecting" },
  closed: { tone: "danger", label: "Offline" },
};

export function LiveIndicator({ status }: { status: ConnectionStatus }) {
  const { tone, label } = CONNECTION_LABEL[status];
  return <StatusDot tone={tone} label={`Live updates: ${label.toLowerCase()}`} showLabel={false} pulse={status === "reconnecting"} />;
}

function Brand() {
  return (
    <div className="flex items-center gap-2.5">
      <span aria-hidden="true" className="flex size-8 items-center justify-center rounded-full border border-accent/60 bg-accent/15 text-accent">
        <Icon name="voice" className="size-4" />
      </span>
      <span className="font-display text-lg font-semibold tracking-tight text-ink">JARVIS AI</span>
    </div>
  );
}

interface NavListProps {
  items: readonly NavItem[];
  awaitingApproval: number;
  onNavigate?: () => void;
}

function NavList({ items, awaitingApproval, onNavigate }: NavListProps) {
  return (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {items.map((item) => (
        <NavLink
          key={item.id}
          to={item.path}
          onClick={onNavigate}
          className={({ isActive }) =>
            cn(
              "flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition",
              isActive ? "bg-raised text-ink ring-1 ring-accent" : "text-muted hover:bg-raised hover:text-ink",
            )
          }
        >
          {({ isActive }) => (
            <>
              <Icon name={item.icon} className={cn("size-4", isActive ? "text-accent" : "")} />
              <span className="flex-1">{item.label}</span>
              {item.id === "tasks" && awaitingApproval > 0 ? (
                <Badge tone="warning">
                  <span className="sr-only">waiting for approval:</span>
                  {awaitingApproval}
                </Badge>
              ) : null}
            </>
          )}
        </NavLink>
      ))}
    </nav>
  );
}

/**
 * Signed-in layout. Desktop shows a customisable sidebar. Phone widths use a top bar that opens
 * the same navigation as a bottom sheet. The events stream is opened here, once per session.
 */
export function AppShell() {
  const navigate = useNavigate();
  const auth = useAuth();
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const settings = useSettingsQuery();
  const taskCounts = useTaskCountsQuery();
  const order = useUiStore((state) => state.sidebarOrder);
  const hidden = useUiStore((state) => state.hiddenNav);
  const [navOpen, setNavOpen] = useState(false);
  const [customizeOpen, setCustomizeOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);

  // Ctrl+K or Cmd+K opens the palette from anywhere in the shell.
  useEffect(() => {
    const onKey = (event: KeyboardEvent): void => {
      if (!isPaletteShortcut(event)) return;
      event.preventDefault();
      setPaletteOpen((open) => !open);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useAppearanceSync(settings.data?.appearance);

  const notificationSettings = settings.data?.notifications;
  const onLiveEvent = useCallback(
    (event: LiveEvent) => {
      if (event.type === "approval.requested") {
        toast({ tone: "info", title: "Approval needed", description: `${event.data.title}. Open Tasks to approve or deny it.` });
      }
      // Pop-up alerts only reach the viewer while the tab is hidden. The rules live in browserNotifications.ts.
      if (notificationSettings) {
        const alert = browserAlertFor(event, notificationSettings, {
          hidden: typeof document !== "undefined" && document.hidden,
          timeZone: settings.data?.timezone ?? null,
          taskTitle: (taskId) => queryClient.getQueryData<TaskDetailResponse>(qk.task(taskId))?.task.title ?? null,
        });
        if (alert) showBrowserAlert(alert);
      }
    },
    [toast, notificationSettings, queryClient, settings.data?.timezone],
  );
  const liveStatus = useEventStream({ onEvent: onLiveEvent });

  const items = useMemo(() => visibleNavItems(order, hidden, NAV_ITEMS), [order, hidden]);
  const awaiting = taskCounts.data?.awaiting_approval ?? 0;

  const signOut = (): void => {
    auth.logout.mutate(undefined, {
      onSettled: () => navigate("/login", { replace: true }),
    });
  };

  return (
    <div className="flex min-h-screen">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[70] focus:rounded-md focus:bg-accent focus:px-3 focus:py-2 focus:text-on-accent"
      >
        Skip to content
      </a>

      <aside aria-label="Sidebar" className="sticky top-0 hidden h-screen w-64 shrink-0 flex-col gap-6 overflow-y-auto border-r border-line bg-surface/80 p-4 backdrop-blur md:flex">
        <Brand />
        <div className="flex-1">
          <NavList items={items} awaitingApproval={awaiting} />
        </div>
        <div className="flex flex-col gap-3 border-t border-line pt-4">
          <div className="flex items-center justify-between gap-2">
            <StatusDot tone={CONNECTION_LABEL[liveStatus].tone} label={CONNECTION_LABEL[liveStatus].label} />
          </div>
          <p className="truncate text-xs text-muted" title={auth.user?.email}>
            {auth.user?.email ?? ""}
          </p>
          <div className="flex gap-2">
            <Button size="sm" variant="secondary" className="flex-1" onClick={() => setCustomizeOpen(true)}>
              Customise
            </Button>
            <Button size="sm" variant="ghost" onClick={signOut} loading={auth.logout.isPending}>
              Sign out
            </Button>
          </div>
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b border-line bg-canvas/85 px-4 backdrop-blur md:hidden">
          <Brand />
          <div className="flex items-center gap-2">
            <LiveIndicator status={liveStatus} />
            <IconButton label="Open navigation" icon={<Icon name="menu" />} onClick={() => setNavOpen(true)} />
          </div>
        </header>

        <main id="main-content" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-6 outline-none sm:px-6 lg:px-8">
          <Outlet />
        </main>
      </div>

      <Dialog open={navOpen} onClose={() => setNavOpen(false)} placement="bottom" title="Navigation" description="Go to a page.">
        <NavList items={items} awaitingApproval={awaiting} onNavigate={() => setNavOpen(false)} />
        <div className="mt-4 flex gap-2 border-t border-line pt-4">
          <Button variant="secondary" className="flex-1" onClick={() => { setNavOpen(false); setCustomizeOpen(true); }}>
            Customise sidebar
          </Button>
          <Button variant="ghost" onClick={signOut}>
            Sign out
          </Button>
        </div>
      </Dialog>

      <SidebarCustomizeDialog open={customizeOpen} onClose={() => setCustomizeOpen(false)} items={NAV_ITEMS} />
      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        navItems={NAV_ITEMS}
        onRun={(command) => {
          setPaletteOpen(false);
          navigate(command.path);
        }}
      />
      <WakeWordListener />
    </div>
  );
}
