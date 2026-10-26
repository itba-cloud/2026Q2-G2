/* A real tablist -- arrow keys move between tabs and a screen reader announces
 * the selection -- dressed as a segmented control, because these sit in a card
 * header where an underlined tab strip has nothing to sit on. */
export function Tabs<T extends string>({
  value,
  onChange,
  options,
  label,
}: {
  value: T;
  onChange: (value: T) => void;
  options: { value: T; label: string }[];
  label: string;
}) {
  return (
    <div role="tablist" aria-label={label} className="flex rounded-lg bg-plane p-0.5">
      {options.map((option, index) => (
        <button
          key={option.value}
          role="tab"
          aria-selected={value === option.value}
          tabIndex={value === option.value ? 0 : -1}
          onClick={() => onChange(option.value)}
          onKeyDown={(e) => {
            if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
            const step = e.key === "ArrowRight" ? 1 : -1;
            const next = (index + step + options.length) % options.length;
            onChange(options[next].value);
          }}
          className={`min-h-9 whitespace-nowrap rounded-[0.4rem] px-3.5 text-[15px] font-medium transition-colors ${
            value === option.value
              ? "bg-surface text-ink shadow-[0_1px_2px_rgba(20,20,15,0.08)]"
              : "text-ink-2 hover:text-ink"
          }`}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
