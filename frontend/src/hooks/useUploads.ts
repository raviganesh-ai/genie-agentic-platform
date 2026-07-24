import { useCallback, useState } from "react";
import { uploadApi } from "@/services/uploadApi";
import { ApiError } from "@/services/httpClient";
import { useAsyncResource, type AsyncResourceState } from "./useAsyncResource";
import type { UploadRecord, UploadType } from "@/types/upload";
import type { SafeError } from "@/types/common";

export function useUploads(sessionId: string | null): AsyncResourceState<UploadRecord[]> {
  const fetcher = useCallback(() => {
    if (!sessionId) return Promise.reject(new Error("No active session"));
    return uploadApi.list(sessionId);
  }, [sessionId]);

  return useAsyncResource(fetcher, [sessionId], { enabled: Boolean(sessionId) });
}

export interface UploadController {
  upload: (uploadType: UploadType, file: File) => Promise<UploadRecord>;
  uploading: boolean;
  error: SafeError | null;
}

export function useUploadAction(sessionId: string | null): UploadController {
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<SafeError | null>(null);

  const upload = useCallback(
    async (uploadType: UploadType, file: File) => {
      if (!sessionId) throw new Error("No active session");
      setUploading(true);
      setError(null);
      try {
        return await uploadApi.upload(sessionId, uploadType, file);
      } catch (err) {
        setError(err instanceof ApiError ? err : { message: "Unable to upload this file." });
        throw err;
      } finally {
        setUploading(false);
      }
    },
    [sessionId],
  );

  return { upload, uploading, error };
}
