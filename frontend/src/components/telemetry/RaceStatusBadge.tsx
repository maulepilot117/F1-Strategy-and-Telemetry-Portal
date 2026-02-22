/**
 * RaceStatusBadge — shows current race status (Green / SC / VSC / Red Flag).
 *
 * Wired to is_safety_car and last_race_control_message from the SSE state.
 * Pulses when safety car is active to draw attention.
 */

interface RaceStatusBadgeProps {
  isSafetyCar: boolean;
  lastMessage: string;
}

export default function RaceStatusBadge({ isSafetyCar, lastMessage }: RaceStatusBadgeProps) {
  // Determine if this is a VSC (virtual) or full SC from the message text
  const isVSC = lastMessage.toUpperCase().includes("VIRTUAL");
  const isRedFlag = lastMessage.toUpperCase().includes("RED FLAG");

  let label: string;
  let colorClass: string;

  if (isRedFlag) {
    label = "RED FLAG";
    colorClass = "bg-f1-red/20 text-f1-red border-f1-red/50";
  } else if (isSafetyCar && isVSC) {
    label = "VSC";
    colorClass = "bg-f1-yellow/20 text-f1-yellow border-f1-yellow/50 animate-pulse-fast";
  } else if (isSafetyCar) {
    label = "SC";
    colorClass = "bg-f1-yellow/20 text-f1-yellow border-f1-yellow/50 animate-pulse-fast";
  } else {
    label = "GREEN";
    colorClass = "bg-f1-green/20 text-f1-green border-f1-green/50";
  }

  return (
    <span
      className={`px-3 py-1 rounded border text-xs font-f1 font-semibold uppercase tracking-wider ${colorClass}`}
    >
      {label}
    </span>
  );
}
