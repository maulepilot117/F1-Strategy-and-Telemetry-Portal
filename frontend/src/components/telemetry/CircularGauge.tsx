/**
 * CircularGauge — SVG arc gauge for speed and RPM.
 *
 * Renders a 270-degree arc (from 135deg to 405deg) with the value
 * shown as a large number in the center.  The arc fills proportionally
 * using SVG stroke-dasharray/dashoffset animation.
 */

interface CircularGaugeProps {
  value: number;
  maxValue: number;   // 360 for speed, 15000 for RPM
  label: string;
  unit: string;       // "km/h" or "RPM"
  color: string;      // CSS color string like "var(--color-f1-green)"
  size?: number;      // SVG viewBox size (default 120)
}

export default function CircularGauge({
  value,
  maxValue,
  label,
  unit,
  color,
  size = 120,
}: CircularGaugeProps) {
  const radius = (size - 12) / 2;  // leave room for stroke width
  const cx = size / 2;
  const cy = size / 2;

  // Arc spans 270 degrees (from 135° to 405°)
  const arcDegrees = 270;
  const circumference = 2 * Math.PI * radius;
  // Only use 270/360 of the circle
  const arcLength = (arcDegrees / 360) * circumference;

  // How much of the arc to fill
  const pct = Math.min(1, Math.max(0, value / maxValue));
  const filledLength = pct * arcLength;
  const offset = arcLength - filledLength;

  // Rotate so the gap is at the bottom (centered).
  // Arc starts at 3 o'clock (0°), we want it to start at 7:30 (135°).
  const rotation = 135;

  return (
    <div className="flex flex-col items-center">
      <svg
        viewBox={`0 0 ${size} ${size}`}
        className="w-full h-auto"
        style={{ maxWidth: size }}
      >
        {/* Background arc (unfilled) */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke="var(--color-f1-dark)"
          strokeWidth={6}
          strokeDasharray={`${arcLength} ${circumference}`}
          strokeDashoffset={0}
          strokeLinecap="round"
          transform={`rotate(${rotation} ${cx} ${cy})`}
        />
        {/* Filled arc */}
        <circle
          cx={cx}
          cy={cy}
          r={radius}
          fill="none"
          stroke={color}
          strokeWidth={6}
          strokeDasharray={`${arcLength} ${circumference}`}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform={`rotate(${rotation} ${cx} ${cy})`}
          className="transition-[stroke-dashoffset] duration-300"
        />
        {/* Center value */}
        <text
          x={cx}
          y={cy - 2}
          textAnchor="middle"
          dominantBaseline="central"
          className="fill-f1-white font-f1 font-bold"
          style={{ fontSize: size * 0.22 }}
        >
          {Math.round(value)}
        </text>
        {/* Unit label below value */}
        <text
          x={cx}
          y={cy + size * 0.15}
          textAnchor="middle"
          dominantBaseline="central"
          className="fill-f1-muted font-f1"
          style={{ fontSize: size * 0.09 }}
        >
          {unit}
        </text>
      </svg>
      <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1 -mt-1">
        {label}
      </span>
    </div>
  );
}
