import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Route, Routes } from "react-router-dom";
import { LandingPage } from "@/features/landing/LandingPage";
import { useSessionContext } from "@/state/SessionContext";
import { mockFetchSequence, renderWithProviders } from "./testUtils";

function ResumeTarget(): JSX.Element {
  const { sessionId, selectedModelDeploymentRef } = useSessionContext();
  return <div>Resumed {sessionId} with {selectedModelDeploymentRef}</div>;
}

describe("LandingPage generation model selector", () => {
  it("pre-populates the dropdown with the backend's default model once the catalog loads", async () => {
    mockFetchSequence([
      {
        match: "/models/available",
        response: { default_model: "gpt-5-mini", available_models: ["gpt-5-1", "gpt-5-mini"] },
      },
    ]);

    renderWithProviders(<LandingPage />);

    await waitFor(() => {
      expect(screen.getByRole("combobox")).toHaveTextContent("gpt-5-mini");
    });
  });

  it("restores the saved session and model when a Discovery case is resumed", async () => {
    mockFetchSequence([
      {
        match: "/models/available",
        response: { default_model: "gpt-5-mini", available_models: ["gpt-5-mini"] },
      },
      {
        match: "/discovery",
        response: [
          {
            id: "discovery-1",
            session_id: "session-42",
            model_deployment_ref: "gpt-5-mini",
            status: "gathering_answers",
            analysis_revision: 3,
            source_upload_ids: ["upload-1", "upload-2"],
            updated_at: "2026-09-12T03:00:00Z",
          },
        ],
      },
    ]);

    renderWithProviders(
      <Routes>
        <Route path="/" element={<LandingPage />} />
        <Route path="/discovery" element={<ResumeTarget />} />
      </Routes>,
    );

    expect(await screen.findByText("Discovery revision 3")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Resume" }));

    expect(await screen.findByText("Resumed session-42 with gpt-5-mini")).toBeInTheDocument();
  });

  it("deletes a saved Discovery from the resume list", async () => {
    const fetchMock = mockFetchSequence([
      {
        match: "/sessions/session-42/discovery",
        response: {},
      },
      {
        match: "/models/available",
        response: { default_model: "gpt-5-mini", available_models: ["gpt-5-mini"] },
      },
      {
        match: "/discovery",
        response: [
          {
            id: "discovery-1",
            session_id: "session-42",
            model_deployment_ref: "gpt-5-mini",
            status: "questioning",
            analysis_revision: 3,
            source_upload_ids: ["upload-1"],
            updated_at: "2026-09-12T03:00:00Z",
          },
        ],
      },
    ]);

    renderWithProviders(<LandingPage />);

    expect(await screen.findByText("Discovery revision 3")).toBeInTheDocument();
    await userEvent.setup().click(screen.getByRole("button", { name: "Delete Discovery revision 3" }));

    await waitFor(() => {
      expect(screen.queryByText("Discovery revision 3")).not.toBeInTheDocument();
      expect(fetchMock.mock.calls.some(([input, init]) =>
        new URL(input.toString()).pathname.endsWith("/sessions/session-42/discovery")
        && init?.method === "DELETE",
      )).toBe(true);
    });
  });
});
