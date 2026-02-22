/**
 * TyreCompound — visual tyre badge showing current compound and age.
 *
 * Uses the same compound colours as the F1 broadcast:
 *  - SOFT = red, MEDIUM = yellow, HARD = white, INTERMEDIATE = green, WET = blue
 */

interface TyreCompoundProps {
  compound: string;
  age: number;   // laps on this set
}

/** Map compound name to its broadcast colour (CSS value) */
function compoundBg(compound: string): string {
  switch (compound.toUpperCase()) {
    case "SOFT": return "bg-red-500";
    case "MEDIUM": return "bg-yellow-400";
    case "HARD": return "bg-white";
    case "INTERMEDIATE": return "bg-green-500";
    case "WET": return "bg-blue-500";
    default: return "bg-f1-muted";
  }
}

function compoundText(compound: string): string {
  // HARD uses dark text because its background is white
  return compound.toUpperCase() === "HARD" ? "text-black" : "text-white";
}

export default function TyreCompound({ compound, age }: TyreCompoundProps) {
  return (
    <div className="flex flex-col items-center gap-1">
      <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
        Tyre
      </span>
      <div className="flex items-center gap-2">
        <span
          className={`px-2 py-0.5 rounded text-xs font-f1 font-semibold ${compoundBg(compound)} ${compoundText(compound)}`}
        >
          {compound.charAt(0)}
        </span>
        <span className="text-xs font-mono text-f1-muted tabular-nums">
          {age}L
        </span>
      </div>
    </div>
  );
}
