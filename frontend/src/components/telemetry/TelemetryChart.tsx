/**
 * TelemetryChart — Recharts line chart showing telemetry history.
 *
 * Renders speed over time from the telemetry history ring buffer.
 * Uses useTelemetryHistory() which accumulates samples client-side
 * (not sent from the backend — would balloon SSE payload).
 */

import { LineChart, Line, XAxis, YAxis, ResponsiveContainer } from "recharts";
import { useTelemetryHistory } from "../../hooks/useLiveRace";

interface TelemetryChartProps {
  driverNumber: number | null;
  teamColor: string;
}

export default function TelemetryChart({ driverNumber, teamColor }: TelemetryChartProps) {
  const history = useTelemetryHistory(driverNumber);

  if (history.length < 2) {
    return (
      <div className="h-full flex items-center justify-center text-f1-muted text-xs font-f1">
        Collecting telemetry data...
      </div>
    );
  }

  // Normalize timestamps to seconds-ago for the X axis
  const now = history[history.length - 1].ts;
  const chartData = history.map((h) => ({
    t: Math.round((h.ts - now) / 1000),
    speed: h.speed,
  }));

  return (
    <ResponsiveContainer width="100%" height="100%">
      <LineChart data={chartData} margin={{ top: 4, right: 8, bottom: 4, left: 8 }}>
        <XAxis
          dataKey="t"
          tick={{ fill: "#9CA3AF", fontSize: 9 }}
          tickLine={false}
          axisLine={{ stroke: "#2A2A3E" }}
          tickFormatter={(v: number) => `${v}s`}
        />
        <YAxis
          domain={[0, 370]}
          tick={{ fill: "#9CA3AF", fontSize: 9 }}
          tickLine={false}
          axisLine={{ stroke: "#2A2A3E" }}
          width={32}
        />
        <Line
          type="monotone"
          dataKey="speed"
          stroke={teamColor}
          strokeWidth={1.5}
          dot={false}
          isAnimationActive={false}
        />
      </LineChart>
    </ResponsiveContainer>
  );
}
