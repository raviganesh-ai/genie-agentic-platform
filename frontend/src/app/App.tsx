import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { RequirementsHubPage } from "@/layouts/RequirementsHubPage";
import { OutputsHubPage } from "@/layouts/OutputsHubPage";
import { LandingPage } from "@/features/landing/LandingPage";
import { UploadPage } from "@/features/upload/UploadPage";
import { RequirementDiscoveryPage } from "@/features/requirement-map/RequirementDiscoveryPage";
import { ArchitectureStudioPage } from "@/features/architecture-studio/ArchitectureStudioPage";
import { WorkshopPage } from "@/features/workshop-center/WorkshopPage";
import { ReplayCenterPage } from "@/features/replay-center/ReplayCenterPage";
import { FinalOutputPage } from "@/features/final-output-center/FinalOutputPage";
import { DeployLaunchPage } from "@/features/deploy-launch/DeployLaunchPage";

const router = createBrowserRouter([
  {
    path: "/",
    element: <AppShell />,
    children: [
      { index: true, element: <LandingPage /> },
      { path: "upload", element: <UploadPage /> },
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
          { path: "replay", element: <ReplayCenterPage /> },
          { path: "final", element: <FinalOutputPage /> },
        ],
      },
    ],
  },
]);

export function App(): JSX.Element {
  return <RouterProvider router={router} />;
}
