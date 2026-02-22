/**
 * GearDisplay — large gear number with shift light strip.
 *
 * Shows the current gear as a large centered number.  Above it, a row
 * of "shift lights" illuminate progressively as RPM increases (like the
 * LED strip on an F1 steering wheel).
 */

interface GearDisplayProps {
  gear: number;     // 0 = neutral, 1–8 = forward gears
  rpm: number;      // current RPM (determines shift light count)
  maxRpm?: number;  // default 15000
}

/** Number of shift light segments */
const LIGHT_COUNT = 10;

/** RPM threshold for each light — lights up progressively from green to red */
function lightColor(index: number): string {
  if (index < 4) return "bg-f1-green";
  if (index < 7) return "bg-f1-yellow";
  return "bg-f1-red";
}

export default function GearDisplay({ gear, rpm, maxRpm = 15000 }: GearDisplayProps) {
  // How many lights should be on (proportional to RPM)
  const litCount = Math.floor((rpm / maxRpm) * LIGHT_COUNT);

  return (
    <div className="flex flex-col items-center gap-1">
      {/* Shift light strip */}
      <div className="flex gap-0.5">
        {Array.from({ length: LIGHT_COUNT }, (_, i) => (
          <div
            key={i}
            className={`w-2.5 h-1.5 rounded-sm transition-colors duration-100 ${
              i < litCount ? lightColor(i) : "bg-f1-dark"
            }`}
          />
        ))}
      </div>

      {/* Gear number */}
      <div className="text-5xl font-f1 font-bold text-f1-white leading-none tabular-nums">
        {gear === 0 ? "N" : gear}
      </div>

      <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
        Gear
      </span>
    </div>
  );
}
