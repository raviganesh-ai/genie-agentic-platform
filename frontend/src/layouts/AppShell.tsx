import type { ReactNode } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Text } from "@fluentui/react-components";

const NAV_ITEMS: Array<{ to: string; label: string }> = [
  { to: "/", label: "Landing" },
  { to: "/upload", label: "Upload" },
  { to: "/mission-control", label: "Mission Control" },
  { to: "/agent-arena", label: "Agent Arena" },
  { to: "/collaboration-graph", label: "Collaboration Graph" },
  { to: "/requirements", label: "Requirement Discovery" },
  { to: "/architecture-studio", label: "Architecture Studio" },
  { to: "/workshop", label: "Workshop" },
  { to: "/governance", label: "Governance" },
  { to: "/replay-center", label: "Replay Center" },
  { to: "/final-output", label: "Final Output" },
];

function navLinkStyle(isActive: boolean): React.CSSProperties {
  return {
    display: "block",
    padding: "8px 12px",
    borderRadius: 6,
    textDecoration: "none",
    color: isActive ? "#0b0f14" : "#c7cdd6",
    backgroundColor: isActive ? "#2f83e0" : "transparent",
    fontSize: 14,
    marginBottom: 2,
  };
}

export function AppShell(): JSX.Element {
  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      <nav
        style={{
          width: 240,
          flexShrink: 0,
          backgroundColor: "#11161d",
          borderRight: "1px solid #232a33",
          padding: 16,
        }}
      >
        <Text weight="bold" size={500} style={{ display: "block", marginBottom: 4 }}>
          Genie
        </Text>
        <Text size={200} style={{ display: "block", marginBottom: 20, opacity: 0.7 }}>
          Agentic Experience Center
        </Text>
        {NAV_ITEMS.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            style={({ isActive }) => navLinkStyle(isActive)}
            end={item.to === "/"}
          >
            {item.label}
          </NavLink>
        ))}
      </nav>
      <main style={{ flex: 1, padding: 24, overflowY: "auto" }}>
        <Outlet />
      </main>
    </div>
  );
}

export function PageHeader({
  title,
  subtitle,
  action,
}: {
  title: string;
  subtitle?: string;
  action?: ReactNode;
}): JSX.Element {
  return (
    <div
      style={{
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        marginBottom: 20,
      }}
    >
      <div>
        <Text weight="bold" size={600} style={{ display: "block" }}>
          {title}
        </Text>
        {subtitle ? (
          <Text size={300} style={{ opacity: 0.7 }}>
            {subtitle}
          </Text>
        ) : null}
      </div>
      {action}
    </div>
  );
}
