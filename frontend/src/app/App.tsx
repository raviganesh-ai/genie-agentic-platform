import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { RequirementsHubPage } from "@/layouts/RequirementsHubPage";
import { OutputsHubPage } from "@/layouts/OutputsHubPage";
import { LandingPage } from "@/features/landing/LandingPage";
import { UploadPage } from "@/features/upload/UploadPage";
import { RequirementDiscoveryPage } from "@/features/requirement-map/RequirementDiscoveryPage";
import { ArchitectureStudioPage } from "@/features/architecture-studio/ArchitectureStudioPage";
import { WorkshopPage } from "@/features/workshop-center/WorkshopPage";
import { RequirementFidelityGatePage } from "@/features/requirement-fidelity/RequirementFidelityGatePage";
import { DeployLaunchPage } from "@/features/deploy-launch/DeployLaunchPage";
import { DiscoveryPage } from "@/features/discovery/DiscoveryPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <LandingPage /> },
      { path: "upload", element: <UploadPage /> },
      { path: "discovery", element: <DiscoveryPage /> },
      {
        path: "requirements",
        element: <RequirementsHubPage />,
        children: [{ index: true, element: <RequirementDiscoveryPage /> }],
      },
      { path: "architecture-studio", element: <ArchitectureStudioPage /> },
      { path: "workshop", element: <WorkshopPage /> },
      {
        path: "outputs",
        element: <OutputsHubPage />,
        children: [
          { index: true, element: <DeployLaunchPage /> },
          { path: "fidelity-gate", element: <RequirementFidelityGatePage /> },
        ],
      },
    ],
  },
]);

export function App(): JSX.Element {
  return <RouterProvider router={router} />;
}
