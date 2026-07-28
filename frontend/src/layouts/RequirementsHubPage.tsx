import { NavLink, Outlet } from "react-router-dom";

const TABS: Array<{ to: string; label: string; end?: boolean }> = [
  { to: "/requirements", label: "Discovery", end: true },
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
 * Wraps the whole-session Requirement Discovery map under a single
 * "Requirements" nav step (retains the hub/sub-tab layout for consistency
 * with the Outputs hub, even though it currently has only one tab - the
 * per-epic Requirement Groups feature was retired in favor of the
 * single-orchestrator mission flow).
 */
export function RequirementsHubPage(): JSX.Element {
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
