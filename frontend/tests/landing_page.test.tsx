import { describe, expect, it } from "vitest";
import { screen, waitFor } from "@testing-library/react";
import { LandingPage } from "@/features/landing/LandingPage";
import { mockFetchSequence, renderWithProviders } from "./testUtils";

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
});
