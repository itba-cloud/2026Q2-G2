import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
import { IconChevronDown } from "./icons";

export interface DropdownOption {
  value: string;
  label: string;
  /** A second line, for when the label alone is ambiguous. */
  hint?: string;
}

/* A select we own, because the native one renders as an OS menu that has
 * nothing to do with the rest of the page.
 *
 * Two things make it more than a styled div. It is a real combobox/listbox
 * pair, so arrow keys, Home/End, type-ahead and screen readers all behave the
 * way people expect from a select. And the list renders in a portal, pinned to
 * the trigger's rect: every panel here is a scroll container with
 * `overflow-hidden`, so a popup rendered in place would be clipped by the card
 * it belongs to. */
export function Dropdown({
  id,
  value,
  onChange,
  options,
  placeholder = "Elegí una opción",
  ariaLabel,
  triggerClassName = "",
  renderValue,
  disabled = false,
}: {
  id?: string;
  value: string;
  onChange: (value: string) => void;
  options: DropdownOption[];
  placeholder?: string;
  ariaLabel?: string;
  triggerClassName?: string;
  /** Lets a caller draw a richer trigger (the branch switcher shows three
   * lines) while keeping all the listbox behaviour. */
  renderValue?: (option: DropdownOption | undefined) => ReactNode;
  disabled?: boolean;
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [activeIndex, setActiveIndex] = useState(0);
  const [position, setPosition] = useState<CSSProperties>({});
  const triggerRef = useRef<HTMLButtonElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const typeAhead = useRef({ text: "", at: 0 });

  const selectedIndex = options.findIndex((o) => o.value === value);
  const selected = selectedIndex >= 0 ? options[selectedIndex] : undefined;

  const reposition = useCallback(() => {
    const trigger = triggerRef.current;
    if (!trigger) return;
    const rect = trigger.getBoundingClientRect();
    const gap = 6;
    const spaceBelow = window.innerHeight - rect.bottom - gap;
    const spaceAbove = rect.top - gap;
    // Flip up only when there genuinely is not room below, so the list does
    // not jump sides as the page scrolls.
    const openUp = spaceBelow < 200 && spaceAbove > spaceBelow;
    setPosition({
      position: "fixed",
      left: rect.left,
      minWidth: rect.width,
      maxWidth: Math.max(rect.width, 320),
      maxHeight: Math.min(288, (openUp ? spaceAbove : spaceBelow) - 8),
      ...(openUp ? { bottom: window.innerHeight - rect.top + gap } : { top: rect.bottom + gap }),
    });
  }, []);

  useLayoutEffect(() => {
    if (!isOpen) return;
    reposition();
    // `true` catches scrolls in the panes between us and the viewport, not
    // just the window's own.
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [isOpen, reposition]);

  useEffect(() => {
    if (!isOpen) return;
    listRef.current?.focus();
    // Escape belongs to the innermost thing that is open. Without this the
    // event reached the dialog behind the list and closed the whole form.
    const onEscapeCapture = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      e.stopPropagation();
      setIsOpen(false);
      triggerRef.current?.focus();
    };
    document.addEventListener("keydown", onEscapeCapture, true);
    const onPointerDown = (e: MouseEvent) => {
      const target = e.target as Node;
      if (!listRef.current?.contains(target) && !triggerRef.current?.contains(target)) {
        setIsOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => {
      document.removeEventListener("keydown", onEscapeCapture, true);
      document.removeEventListener("mousedown", onPointerDown);
    };
  }, [isOpen]);

  // Keep the active option in view while arrowing through a long list.
  useEffect(() => {
    if (!isOpen) return;
    listRef.current
      ?.querySelector(`[data-index="${activeIndex}"]`)
      ?.scrollIntoView({ block: "nearest" });
  }, [isOpen, activeIndex]);

  function open() {
    if (disabled) return;
    setActiveIndex(selectedIndex >= 0 ? selectedIndex : 0);
    setIsOpen(true);
  }

  function commit(index: number) {
    const option = options[index];
    if (option) onChange(option.value);
    setIsOpen(false);
    triggerRef.current?.focus();
  }

  function onListKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape" || e.key === "Tab") {
      setIsOpen(false);
      triggerRef.current?.focus();
      return;
    }
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActiveIndex((i) => (i + step + options.length) % options.length);
      return;
    }
    if (e.key === "Home" || e.key === "End") {
      e.preventDefault();
      setActiveIndex(e.key === "Home" ? 0 : options.length - 1);
      return;
    }
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      commit(activeIndex);
      return;
    }
    if (e.key.length === 1) {
      const now = Date.now();
      typeAhead.current.text = now - typeAhead.current.at > 700 ? e.key : typeAhead.current.text + e.key;
      typeAhead.current.at = now;
      const query = typeAhead.current.text.toLowerCase();
      const found = options.findIndex((o) => o.label.toLowerCase().startsWith(query));
      if (found >= 0) setActiveIndex(found);
    }
  }

  const listId = id ? `${id}-listbox` : undefined;

  return (
    <>
      <button
        ref={triggerRef}
        id={id}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        aria-controls={listId}
        aria-label={ariaLabel}
        disabled={disabled}
        onClick={() => (isOpen ? setIsOpen(false) : open())}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            open();
          }
        }}
        className={
          triggerClassName ||
          `flex min-h-11 w-full items-center justify-between gap-2 rounded-lg border bg-surface px-3 py-2 text-left text-[15px] text-ink transition-colors disabled:cursor-not-allowed disabled:bg-plane disabled:text-muted ${
            isOpen ? "border-brand" : "border-line hover:border-brand"
          }`
        }
      >
        {renderValue ? (
          renderValue(selected)
        ) : (
          <>
            <span className={`truncate ${selected ? "" : "text-muted"}`}>
              {selected ? selected.label : placeholder}
            </span>
            <IconChevronDown className="size-4 shrink-0 text-muted" />
          </>
        )}
      </button>

      {isOpen &&
        createPortal(
          <ul
            ref={listRef}
            id={listId}
            role="listbox"
            tabIndex={-1}
            aria-activedescendant={id ? `${id}-opt-${activeIndex}` : undefined}
            onKeyDown={onListKeyDown}
            style={position}
            className="z-50 overflow-y-auto overscroll-contain rounded-xl border border-line bg-surface p-1 shadow-[0_12px_32px_rgba(20,20,15,0.16)] focus:outline-none"
          >
            {options.map((option, index) => {
              const isSelected = option.value === value;
              const isActive = index === activeIndex;
              return (
                <li
                  key={option.value}
                  id={id ? `${id}-opt-${index}` : undefined}
                  data-index={index}
                  role="option"
                  aria-selected={isSelected}
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => commit(index)}
                  className={`flex cursor-pointer items-start gap-2 rounded-lg px-2.5 py-2 text-[15px] ${
                    isActive ? "bg-brand-tint" : ""
                  } ${isSelected ? "font-medium text-brand-ink" : "text-ink"}`}
                >
                  <span aria-hidden="true" className="w-4 shrink-0 text-brand">
                    {isSelected ? "✓" : ""}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate leading-tight">{option.label}</span>
                    {option.hint && (
                      <span className="block truncate text-[13px] leading-tight text-muted">
                        {option.hint}
                      </span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>,
          document.body,
        )}
    </>
  );
}
