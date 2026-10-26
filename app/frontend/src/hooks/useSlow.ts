import { useEffect, useState } from "react";

/** True once `active` has stayed true for `afterMs`. A comparison that answers
 * in a moment shows its skeleton and nothing else; one that is still running
 * after a couple of seconds (a dense radius, computed live) gets to say so,
 * instead of leaving a blank skeleton up for twenty. */
export function useSlow(active: boolean, afterMs = 2_000): boolean {
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (!active) {
      setSlow(false);
      return;
    }
    const timer = setTimeout(() => setSlow(true), afterMs);
    return () => clearTimeout(timer);
  }, [active, afterMs]);
  return slow;
}
