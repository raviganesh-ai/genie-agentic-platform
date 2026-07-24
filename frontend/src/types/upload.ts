/** Mirrors backend/app/models/upload_models.py 1:1. */

export type UploadType = "transcript" | "audio" | "video" | "supporting_document";

export type IngestionStatus = "received" | "queued" | "processing" | "completed" | "failed";

export interface UploadRecord {
  id: string;
  session_id: string;
  upload_type: UploadType;
  file_name: string;
  content_type: string;
  size_bytes: number;
  uploaded_by: string;
  status: IngestionStatus;
  detail: string;
  uploaded_at: string;
  updated_at: string;
}
