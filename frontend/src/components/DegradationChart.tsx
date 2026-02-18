import {
  LineChart,
  Line,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts";
import type { DegradationResponse } from "../types";
import styles from "./DegradationChart.module.css";

interface Props {
  data: DegradationResponse;
}

/* Map compound names to F1 broadcast colors */
const COMPOUND_COLORS: Record<string, string> = {
  SOFT: "var(--compound-soft)",
  MEDIUM: "var(--compound-medium)",
  HARD: "var(--compound-hard)",
};

/* Recharts needs data merged into one array:
   [{tyre_age: 1, SOFT: 0.04, MEDIUM: 0.02}, {tyre_age: 2, ...}, ...]
   We pivot the per-compound curves into this shape. */
function mergeChartData(
  compounds: DegradationResponse["compounds"],
): Record<string, number>[] {
  const byAge: Record<number, Record<string, number>> = {};

  for (const [compound, info] of Object.entries(compounds)) {
    for (const point of info.curve) {
      if (!byAge[point.tyre_age]) {
        byAge[point.tyre_age] = { tyre_age: point.tyre_age };
      }
      byAge[point.tyre_age][compound] = point.avg_delta_s;
    }
  }

  // Sort by tyre age so the chart draws left to right
  return Object.values(byAge).sort((a, b) => a.tyre_age - b.tyre_age);
}

export default function DegradationChart({ data }: Props) {
  const compounds = Object.keys(data.compounds);

  if (compounds.length === 0) {
    return (
      <div className={styles.container}>
        <p className={styles.noData}>
          No degradation data available for this event.
        </p>
      </div>
    );
  }

  const chartData = mergeChartData(data.compounds);

  return (
    <div className={styles.container}>
      {/* Header with title and per-compound degradation rates */}
      <div className={styles.header}>
        <span className={styles.title}>Tyre Degradation</span>
        {compounds.map((c) => (
          <span
            key={c}
            className={styles.rate}
            style={{ color: COMPOUND_COLORS[c] || "#fff" }}
          >
            {c}: {data.compounds[c].degradation_per_lap_s.toFixed(3)} s/lap
          </span>
        ))}
      </div>

      {/* Recharts line chart — one line per compound */}
      <div className={styles.chartWrapper}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={chartData}>
            <CartesianGrid stroke="#2a2a2a" strokeDasharray="3 3" />
            <XAxis
              dataKey="tyre_age"
              label={{
                value: "Tyre Age (laps)",
                position: "insideBottom",
                offset: -5,
                fill: "#888",
              }}
              tick={{ fill: "#888", fontSize: 12 }}
              stroke="#2a2a2a"
            />
            <YAxis
              label={{
                value: "Degradation (s)",
                angle: -90,
                position: "insideLeft",
                offset: 10,
                fill: "#888",
              }}
              tick={{ fill: "#888", fontSize: 12 }}
              stroke="#2a2a2a"
            />
            <Tooltip
              contentStyle={{
                background: "#1a1a1a",
                border: "1px solid #2a2a2a",
                borderRadius: 6,
                color: "#f0f0f0",
              }}
              formatter={(value) => {
                if (typeof value === "number") return [`${value.toFixed(3)}s`];
                return [String(value)];
              }}
              labelFormatter={(label) => `Tyre age: ${label} laps`}
            />
            <Legend wrapperStyle={{ color: "#888", fontSize: 12 }} />
            {compounds.map((compound) => (
              <Line
                key={compound}
                type="monotone"
                dataKey={compound}
                stroke={COMPOUND_COLORS[compound] || "#fff"}
                strokeWidth={2}
                dot={{ r: 3 }}
                activeDot={{ r: 5 }}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
