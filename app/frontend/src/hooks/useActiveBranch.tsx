import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import type { OrgBranch } from "../types";

export const ACTIVE_BRANCH_STORAGE_KEY = "lgdc_active_branch";
const STORAGE_KEY = ACTIVE_BRANCH_STORAGE_KEY;

function readStored(): OrgBranch | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    return raw ? (JSON.parse(raw) as OrgBranch) : null;
  } catch {
    return null;
  }
}

interface ActiveBranchContextValue {
  activeBranch: OrgBranch | null;
  setActiveBranch: (branch: OrgBranch | null) => void;
}

const ActiveBranchContext = createContext<ActiveBranchContextValue | null>(null);

export function ActiveBranchProvider({ children }: { children: ReactNode }) {
  const [activeBranch, setActiveBranchState] = useState<OrgBranch | null>(readStored);

  function setActiveBranch(branch: OrgBranch | null) {
    setActiveBranchState(branch);
    if (branch) localStorage.setItem(STORAGE_KEY, JSON.stringify(branch));
    else localStorage.removeItem(STORAGE_KEY);
  }

  return (
    <ActiveBranchContext.Provider value={{ activeBranch, setActiveBranch }}>
      {children}
    </ActiveBranchContext.Provider>
  );
}

export function useActiveBranch(): ActiveBranchContextValue {
  const ctx = useContext(ActiveBranchContext);
  if (!ctx) throw new Error("useActiveBranch must be used inside <ActiveBranchProvider>");
  return ctx;
}

/** Keeps the stored active branch honest against the org's current
 * subscriptions -- drops it if the org unsubscribed from that branch, and
 * re-syncs the cached label/radius fields when they change. Call once
 * wherever /me/branches is fetched at the top of the tree. */
export function useSyncActiveBranch(orgBranches: OrgBranch[] | undefined) {
  const { activeBranch, setActiveBranch } = useActiveBranch();

  useEffect(() => {
    if (!orgBranches) return;
    if (!activeBranch) {
      if (orgBranches.length > 0) setActiveBranch(orgBranches[0]);
      return;
    }
    const match = orgBranches.find(
      (b) =>
        b.company_id === activeBranch.company_id &&
        b.banner_id === activeBranch.banner_id &&
        b.branch_id === activeBranch.branch_id,
    );
    if (!match) {
      setActiveBranch(orgBranches[0] ?? null);
    } else if (JSON.stringify(match) !== JSON.stringify(activeBranch)) {
      setActiveBranch(match);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [orgBranches]);
}
