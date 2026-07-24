import { RadialBar, RadialBarChart, ResponsiveContainer } from "recharts";
import { statusPalette } from "@/styles/theme";

/**
 * Executive-grade radial confidence/readiness/risk gauge (0-100). Used
 * across Mission Control's Executive Scoreboard, Architecture Studio, and
 * Agent Arena contribution displays for a "professional gamification" feel
 * without cartoonish styling.
 */
export function ConfidenceGauge({
  value,
  label,
  size = 120,
}: {
  value: number;
  label: string;
  size?: number;
}): JSX.Element {
  const clamped = Math.max(0, Math.min(100, value));
  const color =
    clamped >= 75
      ? statusPalette.compliant
      : clamped >= 40
        ? statusPalette.warning
        : statusPalette.blocked;

  const data = [{ name: label, value: clamped, fill: color }];

  return (
    <div style={{ width: size, height: size, position: "relative" }}>
      <ResponsiveContainer>
        <RadialBarChart
          width={size}
          height={size}
          cx="50%"
          cy="50%"
          innerRadius="70%"
          outerRadius="100%"
          barSize={10}
          data={data}
          startAngle={90}
          endAngle={-270}
        >
          <RadialBar background dataKey="value" cornerRadius={6} />
        </RadialBarChart>
      </ResponsiveContainer>
      <div
        style={{
          position: "absolute",
          inset: 0,
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <span style={{ fontSize: size * 0.22, fontWeight: 600 }}>{Math.round(clamped)}</span>
        <span style={{ fontSize: size * 0.09, opacity: 0.7 }}>{label}</span>
      </div>
    </div>
  );
}
