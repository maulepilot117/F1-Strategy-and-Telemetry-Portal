/**
 * GearDisplay — large gear number with shift light strip.
 *
 * Matches the F1 broadcast steering wheel display:
 *  - "GEAR" label on top
 *  - Row of 10 shift lights: green → yellow → red as RPM rises
 *  - Large centered gear number
 */

interface GearDisplayProps {
  gear: number;     // 0 = neutral, 1–8 = forward gears
  rpm: number;      // current RPM (determines shift light count)
  maxRpm?: number;  // default 15000
}

/** Number of shift light segments */
const LIGHT_COUNT = 10;

/** Colour for each shift light — green first 4, yellow middle 3, red last 3 */
function lightColor(index: number): string {
  if (index < 4) return "bg-f1-green";
  if (index < 7) return "bg-f1-yellow";
  return "bg-f1-red";
}

export default function GearDisplay({ gear, rpm, maxRpm = 15000 }: GearDisplayProps) {
  const litCount = Math.floor((rpm / maxRpm) * LIGHT_COUNT);

  return (
    <div className="flex flex-col items-center gap-1">
      {/* Label */}
      <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1 font-semibold">
        Gear
      </span>

      {/* Shift light strip */}
      <div className="flex gap-0.5">
        {Array.from({ length: LIGHT_COUNT }, (_, i) => (
          <div
            key={i}
            className={`w-3 h-2 rounded-sm transition-colors duration-100 ${
              i < litCount ? lightColor(i) : "bg-f1-dark"
            }`}
          />
        ))}
      </div>

      {/* Large gear number */}
      <div className="text-6xl font-f1 font-bold text-f1-white leading-none tabular-nums">
        {gear === 0 ? "N" : gear}
      </div>
    </div>
  );
}
