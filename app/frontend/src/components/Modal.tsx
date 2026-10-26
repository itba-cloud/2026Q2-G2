import { useEffect, useRef, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { IconClose } from "./icons";

/* A dialog, in a portal. The portal is not decoration: every panel on this
 * site is a scroll container with `overflow-hidden`, so anything rendered in
 * place would be clipped by the card it belongs to.
 *
 * Escape and a backdrop click both close it, focus moves in on open and back
 * to whatever opened it on close, and the page behind does not scroll. */
export function Modal({
  isOpen,
  onClose,
  title,
  description,
  children,
  footer,
  size = "md",
}: {
  isOpen: boolean;
  onClose: () => void;
  title: string;
  description?: string;
  children: ReactNode;
  footer?: ReactNode;
  size?: "md" | "lg";
}) {
  const panelRef = useRef<HTMLDivElement>(null);
  const openerRef = useRef<Element | null>(null);
  // The latest onClose, read at the moment Escape is pressed. Callers pass an
  // inline function, a new one on every render: with it among the effect's
  // dependencies, every keystroke in a field re-ran the effect, and focus
  // jumped to the first control (the close button) after each character.
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!isOpen) return;
    openerRef.current = document.activeElement;

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") onCloseRef.current();
    };
    document.addEventListener("keydown", onKeyDown);
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    // Focus the first control rather than the panel, so a keyboard user lands
    // on something they can act on.
    const focusable = panelRef.current?.querySelector<HTMLElement>(
      "input, select, textarea, button, [href], [tabindex]:not([tabindex='-1'])",
    );
    focusable?.focus();

    return () => {
      document.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      (openerRef.current as HTMLElement | null)?.focus?.();
    };
  }, [isOpen]);

  if (!isOpen) return null;

  return createPortal(
    <div className="fixed inset-0 z-50 flex items-end justify-center p-0 sm:items-center sm:p-4">
      <button
        type="button"
        aria-hidden="true"
        tabIndex={-1}
        onClick={onClose}
        className="absolute inset-0 bg-ink/40 backdrop-blur-[1px]"
      />
      <div
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        className={`relative flex max-h-[90dvh] w-full flex-col overflow-hidden rounded-t-2xl border border-line bg-surface shadow-[0_16px_48px_rgba(20,20,15,0.22)] sm:rounded-2xl ${
          size === "lg" ? "sm:max-w-2xl" : "sm:max-w-lg"
        }`}
      >
        <div className="flex shrink-0 items-start gap-3 border-b border-line-soft px-5 py-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-lg font-semibold tracking-tight text-ink">{title}</h2>
            {description && <p className="mt-0.5 text-[15px] text-ink-2">{description}</p>}
          </div>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1.5 -mt-1 inline-flex size-9 shrink-0 items-center justify-center rounded-lg text-muted hover:bg-plane hover:text-ink"
          >
            <IconClose className="size-5" />
            <span className="sr-only">Cerrar</span>
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>

        {footer && (
          <div className="flex shrink-0 flex-wrap items-center justify-end gap-2 border-t border-line-soft bg-plane px-5 py-3">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}
