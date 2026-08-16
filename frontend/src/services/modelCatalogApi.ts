import { apiFetch } from "./httpClient";
import type { ModelCatalogResponse } from "@/types/modelCatalog";

export const modelCatalogApi = {
  getAvailable(): Promise<ModelCatalogResponse> {
    return apiFetch<ModelCatalogResponse>("/models/available");
  },
};
