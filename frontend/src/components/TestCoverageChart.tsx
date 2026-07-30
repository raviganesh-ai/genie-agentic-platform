import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { statusPalette } from "@/styles/theme";

/**
 * Executive-grade horizontal bar chart contrasting two real, directly-
 * counted quantities from the Test Generation Agent's own output: how many
 * test cases it actually wrote (fenced code blocks) versus how many
 * structural coverage gaps it flagged as findings. Deliberately never shows
 * an invented "coverage percentage" - only counts the agent itself stated
 * or that were counted directly from its own output (see
 * ``app.services.peer_review_service._count_code_blocks``).
 */
export function TestCoverageChart({
  testsGenerated,
  coverageGapsFound,
}: {
  testsGenerated: number;
  coverageGapsFound: number;
}): JSX.Element {
  const data = [
    { name: "Tests Generated", value: testsGenerated, fill: statusPalette.compliant },
    {
      name: "Coverage Gaps Found",
      value: coverageGapsFound,
      fill: coverageGapsFound > 0 ? statusPalette.warning : statusPalette.compliant,
    },
  ];

  return (
    <div style={{ width: "100%", height: 140 }}>
      <ResponsiveContainer>
        <BarChart data={data} layout="vertical" margin={{ left: 8, right: 24, top: 8, bottom: 8 }}>
          <CartesianGrid strokeDasharray="3 3" horizontal={false} />
          <XAxis type="number" allowDecimals={false} />
          <YAxis type="category" dataKey="name" width={140} />
          <Tooltip />
          <Bar dataKey="value" radius={[0, 4, 4, 0]}>
            {data.map((entry) => (
              <Cell key={entry.name} fill={entry.fill} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
