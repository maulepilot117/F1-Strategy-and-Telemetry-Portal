/**
 * TyreCompound — circular tyre badge showing current compound and age.
 *
 * Styled as a circular badge (matching the F1 broadcast look):
 *  - SOFT = red, MEDIUM = yellow, HARD = white, INTERMEDIATE = green, WET = blue
 * The compound initial letter sits inside a circle, with lap age shown below.
 */

interface TyreCompoundProps {
  compound: string;
  age: number;   // laps on this set
}

/** Map compound name to its broadcast colour (hex) */
function compoundHex(compound: string): string {
  switch (compound.toUpperCase()) {
    case "SOFT": return "#ef4444";
    case "MEDIUM": return "#facc15";
    case "HARD": return "#ffffff";
    case "INTERMEDIATE": return "#22c55e";
    case "WET": return "#3b82f6";
    default: return "#9CA3AF";
  }
}

export default function TyreCompound({ compound, age }: TyreCompoundProps) {
  const hex = compoundHex(compound);
  const isHard = compound.toUpperCase() === "HARD";

  return (
    <div className="flex flex-col items-center gap-1.5">
      {/* Circular badge with compound letter — matches F1 broadcast style */}
      <div
        className="w-10 h-10 rounded-full flex items-center justify-center text-lg font-f1 font-bold border-2"
        style={{
          backgroundColor: `${hex}22`,
          borderColor: hex,
          color: isHard ? "#ffffff" : hex,
        }}
      >
        {compound.charAt(0)}
      </div>
      <div className="text-center">
        <div className="text-[9px] uppercase tracking-wider text-f1-muted font-f1">
          Tyre
        </div>
        <div className="text-sm font-f1 font-bold text-f1-white">
          {age} <span className="text-f1-muted font-normal text-xs">Laps</span>
        </div>
      </div>
    </div>
  );
}
