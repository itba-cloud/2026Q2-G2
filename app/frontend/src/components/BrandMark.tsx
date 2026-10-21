/* The wordmark. LGDC reads góndolas: the glyph is a shelf of price bars of
 * different lengths, which is the one picture the whole product is about. */
export function BrandMark({
  size = "md",
  tone = "brand",
}: {
  size?: "sm" | "md" | "lg";
  /** On a teal ground the teal tile disappears, so it inverts. */
  tone?: "brand" | "on-brand";
}) {
  const tile = { sm: "size-9 rounded-[0.625rem]", md: "size-11 rounded-xl", lg: "size-14 rounded-2xl" }[size];
  const colours =
    tone === "brand"
      ? "bg-brand text-white shadow-[0_1px_2px_rgba(15,123,108,0.35)]"
      : "bg-white/15 text-white ring-1 ring-inset ring-white/25";
  return (
    <span aria-hidden="true" className={`grid shrink-0 place-items-center ${colours} ${tile}`}>
      <svg viewBox="0 0 24 24" fill="none" className="size-[62%]">
        <path
          d="M5 6.5h11M5 12h7M5 17.5h13"
          stroke="currentColor"
          strokeWidth="2.6"
          strokeLinecap="round"
        />
      </svg>
    </span>
  );
}

export function Wordmark({ tagline = true }: { tagline?: boolean }) {
  return (
    <span className="flex min-w-0 items-center gap-3">
      <BrandMark />
      <span className="min-w-0">
        <span className="block text-xl font-semibold leading-tight tracking-tight text-ink">
          LGDC
        </span>
        {tagline && (
          <span className="block truncate text-xs leading-tight text-muted">
            Precios comparados
          </span>
        )}
      </span>
    </span>
  );
}
