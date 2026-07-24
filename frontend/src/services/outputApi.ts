import { apiFetch } from "./httpClient";
import type { DeliverablePackage, DeliverableType } from "@/types/workflow";

export const outputApi = {
  supportedDeliverableTypes(sessionId: string): Promise<DeliverableType[]> {
    return apiFetch<DeliverableType[]>(`/sessions/${sessionId}/outputs`);
  },
  generateDeliverable(
    sessionId: string,
    workflowRunId: string,
    deliverableType: DeliverableType,
  ): Promise<DeliverablePackage> {
    return apiFetch<DeliverablePackage>(
      `/sessions/${sessionId}/outputs/${workflowRunId}/${deliverableType}`,
    );
  },
};
