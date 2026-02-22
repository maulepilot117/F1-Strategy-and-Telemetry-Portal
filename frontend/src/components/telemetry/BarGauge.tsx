/**
 * BarGauge — horizontal bar with grid lines for throttle/brake display.
 *
 * Shows a filled bar from 0–100% with visual grid lines at 25% intervals.
 * The bar uses a CSS skew transform for the F1 broadcast aesthetic.
 * Brake data from OpenF1 is binary (0 or 100), so the bar shows on/off.
 */

interface BarGaugeProps {
  value: number;       // 0–100
  label: string;
  color: string;       // Tailwind color class like "bg-f1-green"
  maxValue?: number;   // defaults to 100
}

export default function BarGauge({ value, label, color, maxValue = 100 }: BarGaugeProps) {
  const pct = Math.min(100, Math.max(0, (value / maxValue) * 100));

  return (
    <div className="space-y-1">
      <div className="flex justify-between items-center">
        <span className="text-[10px] uppercase tracking-wider text-f1-muted font-f1">
          {label}
        </span>
        <span className="text-xs font-mono text-f1-white tabular-nums">
          {Math.round(value)}%
        </span>
      </div>
      {/* Bar container with skew for the broadcast look */}
      <div className="relative h-3 bg-f1-dark rounded-sm overflow-hidden -skew-x-6">
        {/* Filled portion */}
        <div
          className={`absolute inset-y-0 left-0 ${color} transition-[width] duration-200`}
          style={{ width: `${pct}%` }}
        />
        {/* Grid lines at 25% intervals */}
        {[25, 50, 75].map((mark) => (
          <div
            key={mark}
            className="absolute inset-y-0 w-px bg-f1-border/50"
            style={{ left: `${mark}%` }}
          />
        ))}
      </div>
    </div>
  );
}
