/**
 * DRSIndicator — shows DRS status: open, eligible, or inactive.
 *
 * OpenF1 sends DRS as an integer code.  We categorize using sets:
 *  - Open (10, 12, 14): flap is physically open — green
 *  - Eligible (8): in DRS zone, not yet opened — yellow
 *  - All other values: DRS inactive — dimmed
 */

import { DRS_OPEN_VALUES, DRS_ELIGIBLE_VALUES } from "../../types";

interface DRSIndicatorProps {
  drsCode: number;
}

export default function DRSIndicator({ drsCode }: DRSIndicatorProps) {
  const isOpen = DRS_OPEN_VALUES.has(drsCode);
  const isEligible = DRS_ELIGIBLE_VALUES.has(drsCode);

  let statusText: string;
  let colorClass: string;

  if (isOpen) {
    statusText = "OPEN";
    colorClass = "text-f1-green bg-f1-green/20 border-f1-green/50";
  } else if (isEligible) {
    statusText = "ELIGIBLE";
    colorClass = "text-f1-yellow bg-f1-yellow/20 border-f1-yellow/50";
  } else {
    statusText = "OFF";
    colorClass = "text-f1-muted bg-f1-dark border-f1-border";
  }

  return (
    <div className="flex flex-col items-center gap-1">
      <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
        DRS
      </span>
      <div
        className={`px-3 py-1 rounded border text-xs font-f1 font-semibold tracking-wider ${colorClass} transition-colors duration-200`}
      >
        {statusText}
      </div>
    </div>
  );
}
