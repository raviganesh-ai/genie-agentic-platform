import { NavLink, Outlet } from "react-router-dom";

const TABS: Array<{ to: string; label: string; end?: boolean }> = [
  { to: "/outputs", label: "Deploy & Launch", end: true },
  { to: "/outputs/fidelity-gate", label: "Requirement Fidelity Gate" },
];

function tabStyle(isActive: boolean): React.CSSProperties {
  return {
    padding: "8px 14px",
    textDecoration: "none",
    color: isActive ? "#2f83e0" : "#c7cdd6",
    borderBottom: isActive ? "2px solid #2f83e0" : "2px solid transparent",
    fontSize: 14,
    fontWeight: isActive ? 600 : 400,
  };
}

/**
 * Hosts Deploy & Launch and the Requirement Fidelity Gate under a single
 * "Outputs" nav step, switched via sub-tabs. The Requirement Fidelity Gate
 * tab renders as a modal popup over whichever tab was active rather than a
 * plain page (see RequirementFidelityGatePage).
 */
export function OutputsHubPage(): JSX.Element {
  return (
    <div>
      <div style={{ display: "flex", gap: 4, marginBottom: 16, borderBottom: "1px solid #232a33" }}>
        {TABS.map((tab) => (
          <NavLink key={tab.to} to={tab.to} end={tab.end} style={({ isActive }) => tabStyle(isActive)}>
            {tab.label}
          </NavLink>
        ))}
      </div>
      <Outlet />
    </div>
  );
}
