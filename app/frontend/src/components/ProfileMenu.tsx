import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { IconLogout, IconSettings, IconUser } from "./icons";

function initials(source: string): string {
  const words = source.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return "?";
  if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
  return (words[0][0] + words[words.length - 1][0]).toUpperCase();
}

/* The foot of the rail used to be three loose lines of text and a link. It is
 * one control now: who you are, and the two things you can do about it. The
 * panel opens upwards because there is no room below it. */
export function ProfileMenu({ onNavigate }: { onNavigate: () => void }) {
  const { user, logout } = useAuth();
  const [isOpen, setIsOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!isOpen) return;
    const onPointerDown = (e: MouseEvent) => {
      if (!containerRef.current?.contains(e.target as Node)) setIsOpen(false);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") setIsOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [isOpen]);

  if (!user) return null;

  return (
    <div ref={containerRef} className="relative">
      {isOpen && (
        <div
          role="menu"
          aria-label="Cuenta"
          className="absolute bottom-full left-0 z-20 mb-2 w-full min-w-56 overflow-hidden rounded-xl border border-line bg-surface shadow-[0_8px_24px_rgba(20,20,15,0.12)]"
        >
          <div className="border-b border-line-soft px-3 py-2.5">
            <p className="truncate text-[15px] font-medium text-ink">{user.org_name}</p>
            <p className="truncate text-[13px] text-muted">{user.email}</p>
          </div>
          <div className="p-1">
            <Link
              to="/account"
              role="menuitem"
              onClick={() => {
                setIsOpen(false);
                onNavigate();
              }}
              className="flex min-h-10 items-center gap-2.5 rounded-lg px-2.5 text-[15px] text-ink-2 hover:bg-plane hover:text-ink"
            >
              <IconSettings className="size-4 shrink-0 text-muted" />
              Editar perfil
            </Link>
            <button
              type="button"
              role="menuitem"
              onClick={logout}
              className="flex min-h-10 w-full items-center gap-2.5 rounded-lg px-2.5 text-[15px] text-ink-2 hover:bg-plane hover:text-critical"
            >
              <IconLogout className="size-4 shrink-0 text-muted" />
              Cerrar sesión
            </button>
          </div>
        </div>
      )}

      <button
        type="button"
        onClick={() => setIsOpen((v) => !v)}
        aria-expanded={isOpen}
        aria-haspopup="menu"
        className={`flex min-h-12 w-full items-center gap-2.5 rounded-xl px-2.5 text-left transition-colors ${
          isOpen ? "bg-plane" : "hover:bg-plane"
        }`}
      >
        <span className="grid size-8 shrink-0 place-items-center rounded-full bg-brand-soft text-xs font-semibold text-brand-ink">
          {initials(user.org_name || user.email)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-medium leading-tight text-ink">
            {user.org_name}
          </span>
          <span className="block truncate text-xs leading-tight text-muted">{user.email}</span>
        </span>
        <IconUser className="size-4 shrink-0 text-muted" />
      </button>
    </div>
  );
}
