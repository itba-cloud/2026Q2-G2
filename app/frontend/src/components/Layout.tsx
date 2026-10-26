import { useQuery } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { NavLink, useLocation } from "react-router-dom";
import { getUnreadAlertCount } from "../api/endpoints";
import { useAuth } from "../auth/AuthContext";
import { BranchSwitcher } from "./BranchSwitcher";
import { Wordmark } from "./BrandMark";
import { ProfileMenu } from "./ProfileMenu";
import {
  IconBell,
  IconClose,
  IconCompare,
  IconDashboard,
  IconMenu,
  IconPipeline,
  IconStore,
  IconTag,
  IconUpload,
} from "./icons";

/* What a merchant actually uses, in the order they use it. "Mis" is not
 * decoration: every one of these screens can show either your data or the
 * whole SEPA, and the label is what says which one you are on. Sucursales sits
 * below precios because following a branch is setup you do once, while your
 * own prices are the thing you open every morning. */
const NAV_ITEMS = [
  { to: "/", label: "Tablero", Icon: IconDashboard, end: true },
  { to: "/products", label: "Mis precios", Icon: IconTag },
  { to: "/compare", label: "Comparar", Icon: IconCompare },
  { to: "/branches", label: "Mis sucursales", Icon: IconStore },
  { to: "/alerts", label: "Alertas", Icon: IconBell },
  { to: "/upload", label: "Cargar precios", Icon: IconUpload },
] as const;

/* Operating the ingestion is not a merchant's problem -- whether chain 24
 * parsed today says nothing about their prices. It stays reachable, but out of
 * the main list and only for the account owner. */
const ADMIN_ITEMS = [{ to: "/pipeline", label: "Pipeline", Icon: IconPipeline }] as const;

export function Layout({ children }: { children: ReactNode }) {
  const { user } = useAuth();
  const location = useLocation();
  const [isDrawerOpen, setIsDrawerOpen] = useState(false);

  // Navigating is what closes the drawer: tapping a link should reveal the page
  // it led to, not leave the overlay sitting on top of it.
  useEffect(() => setIsDrawerOpen(false), [location.pathname]);

  // Escape closes it too, and the body does not scroll behind the overlay.
  useEffect(() => {
    if (!isDrawerOpen) return;
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsDrawerOpen(false);
    };
    document.addEventListener("keydown", onKeyDown);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = "";
    };
  }, [isDrawerOpen]);

  const { data: unread } = useQuery({
    queryKey: ["alerts", "unread-count"],
    queryFn: getUnreadAlertCount,
    refetchInterval: 60_000,
  });
  const unreadCount = unread?.count ?? 0;

  const sidebar = (
    <SidebarContent
      user={user}
      unreadCount={unreadCount}
      onNavigate={() => setIsDrawerOpen(false)}
    />
  );

  return (
    /* The shell owns the viewport: it is exactly one screen tall and never
     * scrolls. Everything that can overflow -- a table, a feed, a long list --
     * gets its own scroll container inside a page. */
    <div className="flex h-[100dvh] flex-col overflow-hidden bg-plane lg:flex-row">
      {/* First tab stop on every page: jump the navigation, land on content. */}
      <a
        href="#main"
        className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-ink focus:px-4 focus:py-2 focus:text-[15px] focus:font-medium focus:text-white"
      >
        Saltar al contenido
      </a>

      {/* Phone and tablet: a bar with the drawer trigger. */}
      <header className="z-30 flex shrink-0 items-center gap-3 border-b border-line bg-surface px-4 py-2.5 lg:hidden">
        <button
          type="button"
          onClick={() => setIsDrawerOpen(true)}
          aria-expanded={isDrawerOpen}
          aria-controls="app-drawer"
          className="inline-flex size-11 items-center justify-center rounded-lg text-ink-2 hover:bg-plane hover:text-ink"
        >
          <IconMenu />
          <span className="sr-only">Abrir navegación</span>
        </button>
        <Wordmark tagline={false} />
        {unreadCount > 0 && (
          <NavLink
            to="/alerts"
            className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-[#fdeceb] px-2.5 py-1 text-[13px] font-medium text-critical"
          >
            <IconBell className="size-4" />
            {unreadCount}
            <span className="sr-only">alertas sin leer</span>
          </NavLink>
        )}
      </header>

      {isDrawerOpen && (
        <div className="fixed inset-0 z-40 lg:hidden">
          <button
            type="button"
            onClick={() => setIsDrawerOpen(false)}
            className="absolute inset-0 bg-ink/40"
            tabIndex={-1}
          >
            <span className="sr-only">Cerrar navegación</span>
          </button>
          <div
            id="app-drawer"
            role="dialog"
            aria-modal="true"
            aria-label="Navegación"
            className="absolute inset-y-0 left-0 flex w-[17rem] max-w-[85vw] flex-col bg-surface shadow-xl"
          >
            <div className="flex items-center justify-end px-2 pt-2">
              <button
                type="button"
                onClick={() => setIsDrawerOpen(false)}
                autoFocus
                className="inline-flex size-11 items-center justify-center rounded-lg text-ink-2 hover:bg-plane hover:text-ink"
              >
                <IconClose />
                <span className="sr-only">Cerrar navegación</span>
              </button>
            </div>
            {sidebar}
          </div>
        </div>
      )}

      <aside className="hidden lg:flex lg:h-full lg:w-[16.5rem] lg:shrink-0 lg:flex-col lg:border-r lg:border-line lg:bg-surface">
        {sidebar}
      </aside>

      <main
        id="main"
        className="flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden px-4 py-4 sm:px-6 lg:px-7 lg:py-5"
      >
        <div className="mx-auto flex h-full w-full max-w-[92rem] flex-col">{children}</div>
      </main>
    </div>
  );
}

function SidebarContent({
  user,
  unreadCount,
  onNavigate,
}: {
  user: { email: string; org_name: string | null; role: string } | null;
  unreadCount: number;
  onNavigate: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-4 py-3.5">
        <Wordmark />
      </div>

      {/* The branch every screen is scoped to, directly under the wordmark:
          it frames the whole rail below it. The rule under it says so -- the
          navigation below is read through whatever is selected here. */}
      <div className="border-b border-line-soft px-3 pb-4">
        <BranchSwitcher />
      </div>

      {/* Navigation sits directly under the wordmark: it is what the sidebar is
          for. Who you are and which branch is selected are context, not
          destinations, so they live at the foot of the rail. */}
      <nav aria-label="Secciones" className="flex-1 overflow-y-auto px-3 py-4">
        <ul className="space-y-1">
          {NAV_ITEMS.map(({ to, label, Icon, ...rest }) => (
            <li key={to}>
              <NavItem
                to={to}
                label={label}
                Icon={Icon}
                end={"end" in rest ? rest.end : false}
                onNavigate={onNavigate}
                badge={to === "/alerts" && unreadCount > 0 ? unreadCount : undefined}
              />
            </li>
          ))}
        </ul>

        {user?.role === "owner" && (
          <>
            <p className="mt-5 px-3 text-xs font-medium uppercase tracking-wide text-muted">
              Administración
            </p>
            <ul className="mt-1 space-y-1">
              {ADMIN_ITEMS.map(({ to, label, Icon }) => (
                <li key={to}>
                  <NavItem to={to} label={label} Icon={Icon} end={false} onNavigate={onNavigate} />
                </li>
              ))}
            </ul>
          </>
        )}
      </nav>

      <div className="shrink-0 border-t border-line-soft p-2">
        <ProfileMenu onNavigate={onNavigate} />
      </div>
    </div>
  );
}

function NavItem({
  to,
  label,
  Icon,
  end,
  onNavigate,
  badge,
}: {
  to: string;
  label: string;
  Icon: (props: { className?: string }) => ReactNode;
  end: boolean;
  onNavigate: () => void;
  badge?: number;
}) {
  return (
    <NavLink
      to={to}
      end={end}
      onClick={onNavigate}
      className={({ isActive }) =>
        `flex min-h-11 items-center gap-3 rounded-lg px-3 text-[15px] font-medium transition-colors ${
          isActive ? "bg-brand-soft text-brand-ink" : "text-ink-2 hover:bg-plane hover:text-ink"
        }`
      }
    >
      {({ isActive }) => (
        <>
          <Icon className={isActive ? "size-5 shrink-0 text-brand" : "size-5 shrink-0 text-muted"} />
          <span className="truncate">{label}</span>
          {badge !== undefined && (
            <span className="ml-auto inline-flex min-w-5 justify-center rounded-full bg-critical px-1.5 py-0.5 text-xs font-semibold text-white">
              {badge}
              <span className="sr-only"> alertas sin leer</span>
            </span>
          )}
        </>
      )}
    </NavLink>
  );
}
