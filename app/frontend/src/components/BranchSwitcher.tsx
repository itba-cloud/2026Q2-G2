import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { getMyBranches } from "../api/endpoints";
import { useActiveBranch, useSyncActiveBranch } from "../hooks/useActiveBranch";
import type { OrgBranch } from "../types";
import { Dropdown } from "./Dropdown";
import { toNumber } from "../lib/format";
import { formatRadius } from "../lib/radius";
import { IconChevronDown, IconStore } from "./icons";

function keyOf(b: { company_id: number; banner_id: number; branch_id: string }): string {
  return `${b.company_id}-${b.banner_id}-${b.branch_id}`;
}

/* Which branch you are looking at is app-wide context, not a dashboard filter:
 * it changes what /alerts, a product page and the tablero all mean. So it sits
 * in the rail with the navigation, once, instead of as a stray dropdown in the
 * corner of one screen.
 *
 * The control underneath is a real <select> laid over the card at zero opacity.
 * That buys the native listbox, keyboard handling and mobile picker for free;
 * only the presentation is ours. */
export function BranchSwitcher() {
  const { activeBranch, setActiveBranch } = useActiveBranch();
  const branchesQuery = useQuery({ queryKey: ["me", "branches"], queryFn: getMyBranches });
  useSyncActiveBranch(branchesQuery.data);

  const branches = branchesQuery.data ?? [];

  if (branchesQuery.isLoading) {
    return <div className="h-14 animate-pulse rounded-xl bg-line/60" aria-hidden="true" />;
  }

  if (branches.length === 0) {
    return (
      <Link
        to="/branches"
        className="flex min-h-14 items-center gap-2.5 rounded-xl border border-dashed border-axis px-3 text-[15px] text-ink-2 hover:border-brand hover:bg-brand-tint hover:text-brand-ink"
      >
        <IconStore className="size-5 shrink-0 text-muted" />
        <span>Elegí una sucursal</span>
      </Link>
    );
  }

  const current = activeBranch ?? branches[0];

  return (
    <Dropdown
      id="branch-switcher"
      ariaLabel="Sucursal activa"
      value={keyOf(current)}
      onChange={(next) => {
        const found = branches.find((b: OrgBranch) => keyOf(b) === next);
        if (found) setActiveBranch(found);
      }}
      options={branches.map((b) => ({
        value: keyOf(b),
        label: b.label || b.name,
        hint: `${b.banner_name} · ${formatRadius(toNumber(b.radius_km))}`,
      }))}
      triggerClassName="group flex w-full items-center gap-2 rounded-xl border border-line bg-plane px-3 py-2 text-left transition-colors hover:border-brand hover:bg-brand-tint"
      renderValue={() => (
        <>
          <span className="min-w-0 flex-1">
            <span className="block text-[10px] font-medium uppercase tracking-wide text-muted">
              Sucursal activa
            </span>
            <span className="block truncate text-base font-semibold leading-tight text-ink">
              {current.label || current.name}
            </span>
            <span className="block truncate text-[13px] leading-tight text-muted">
              {current.banner_name} · {formatRadius(toNumber(current.radius_km))}
            </span>
          </span>
          <IconChevronDown className="size-4 shrink-0 text-muted group-hover:text-brand" />
        </>
      )}
    />
  );
}
