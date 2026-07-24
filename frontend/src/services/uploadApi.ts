import { apiFetch } from "./httpClient";
import type { IngestionStatus, UploadRecord, UploadType } from "@/types/upload";

export const uploadApi = {
  upload(sessionId: string, uploadType: UploadType, file: File): Promise<UploadRecord> {
    const form = new FormData();
    form.append("file", file);
    return apiFetch<UploadRecord>(`/sessions/${sessionId}/uploads/${uploadType}`, {
      method: "POST",
      body: form,
      isFormData: true,
    });
  },
  list(sessionId: string): Promise<UploadRecord[]> {
    return apiFetch<UploadRecord[]>(`/sessions/${sessionId}/uploads`);
  },
  get(sessionId: string, uploadId: string): Promise<UploadRecord> {
    return apiFetch<UploadRecord>(`/sessions/${sessionId}/uploads/${uploadId}`);
  },
  getIngestionStatus(sessionId: string, uploadId: string): Promise<UploadRecord> {
    return apiFetch<UploadRecord>(`/sessions/${sessionId}/ingestion/${uploadId}`);
  },
  updateIngestionStatus(
    sessionId: string,
    uploadId: string,
    status: IngestionStatus,
    detail = "",
  ): Promise<UploadRecord> {
    return apiFetch<UploadRecord>(`/sessions/${sessionId}/ingestion/${uploadId}`, {
      method: "PATCH",
      body: { status, detail },
    });
  },
};
