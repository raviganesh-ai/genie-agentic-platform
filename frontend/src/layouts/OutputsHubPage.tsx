import { Outlet } from "react-router-dom";

/**
 * Hosts Deploy & Launch under the "Outputs" nav step. Previously also
 * hosted a separate Requirement Fidelity Gate sub-tab, but that step was
 * removed in favor of the Deploy & Launch pipeline's own informational
 * Security Copilot scan / FinOps cost report steps - so this is now a
 * thin wrapper around the single Deploy & Launch page.
 */
export function OutputsHubPage(): JSX.Element {
  return (
    <div>
      <Outlet />
    </div>
  );
}

