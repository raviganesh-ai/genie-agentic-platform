import { createBrowserRouter, RouterProvider } from "react-router-dom";
import { AppShell } from "@/layouts/AppShell";
import { AuthGate } from "@/features/auth/AuthGate";
import { LoginPage } from "@/features/auth/LoginPage";
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
import { RepositoryConnectionPage } from "@/features/repository-connections/RepositoryConnectionPage";
import { DependencyMappingPage } from "@/features/dependency-mapping/DependencyMappingPage";
import { IqCollaborationPage } from "@/features/iq/IqCollaborationPage";
import { ModernizationPage } from "@/features/modernization/ModernizationPage";
import { PhaseTrackingPage } from "@/features/phase-tracking/PhaseTrackingPage";
import { PlatformConfigPage } from "@/features/platform-config/PlatformConfigPage";
import { ProductionPromotionPage } from "@/features/production-promotion/ProductionPromotionPage";

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <AuthGate>
        <AppShell />
      </AuthGate>
    ),
    children: [
      { index: true, element: <LandingPage /> },
      { path: "upload", element: <UploadPage /> },
      { path: "discovery", element: <DiscoveryPage /> },
      { path: "repository-connections", element: <RepositoryConnectionPage /> },
      { path: "dependency-mapping", element: <DependencyMappingPage /> },
      { path: "iq-collaboration", element: <IqCollaborationPage /> },
      { path: "modernization", element: <ModernizationPage /> },
      { path: "phases", element: <PhaseTrackingPage /> },
      { path: "production-promotion", element: <ProductionPromotionPage /> },
      { path: "configure", element: <PlatformConfigPage /> },
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
