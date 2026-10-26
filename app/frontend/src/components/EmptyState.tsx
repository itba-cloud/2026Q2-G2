import type { ReactNode } from "react";

/* An empty state says what is missing and what to do about it. The action is
 * the point: a dead end with no next step is the thing this replaces.
 *
 * Two treatments, and the difference matters. `dashed` is an invitation --
 * a slot you can fill right here -- so it borrows the dashed outline that
 * drop zones and "add" affordances use. `quiet` is a report: nothing has
 * happened yet and nothing on this panel will change that. Giving a report a
 * dashed border makes people try to drop things on it.
 *
 * `fill` centres it in whatever pane is holding it. Empty panes are the
 * largest thing on a locked-to-the-viewport screen, and a message pinned to
 * the top of one leaves the rest of it looking broken. */
export function EmptyState({
  title,
  description,
  action,
  icon,
  variant = "dashed",
  fill = false,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
  icon?: ReactNode;
  variant?: "dashed" | "quiet";
  fill?: boolean;
}) {
  const frame =
    variant === "dashed"
      ? "rounded-xl border border-dashed border-axis bg-surface px-6 py-10"
      : "px-6 py-10";

  const body = (
    <div className={`flex flex-col items-center gap-3 text-center ${frame}`}>
      {icon && <div className="text-muted">{icon}</div>}
      <p className="text-base font-medium text-ink">{title}</p>
      {description && <p className="max-w-md text-[15px] text-ink-2">{description}</p>}
      {action && <div className="mt-1">{action}</div>}
    </div>
  );

  if (!fill) return body;
  return (
    <div className="flex h-full min-h-[16rem] items-center justify-center p-4">{body}</div>
  );
}
