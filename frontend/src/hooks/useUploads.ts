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
  remove: (uploadId: string) => Promise<void>;
  uploading: boolean;
  removingUploadIds: ReadonlySet<string>;
  error: SafeError | null;
}

export function useUploadAction(sessionId: string | null): UploadController {
  const [uploading, setUploading] = useState(false);
  const [removingUploadIds, setRemovingUploadIds] = useState<Set<string>>(new Set());
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

  const remove = useCallback(
    async (uploadId: string) => {
      if (!sessionId) throw new Error("No active session");
      setRemovingUploadIds((current) => new Set(current).add(uploadId));
      setError(null);
      try {
        await uploadApi.delete(sessionId, uploadId);
      } catch (err) {
        setError(err instanceof ApiError ? err : { message: "Unable to remove this file." });
        throw err;
      } finally {
        setRemovingUploadIds((current) => {
          const next = new Set(current);
          next.delete(uploadId);
          return next;
        });
      }
    },
    [sessionId],
  );

  return { upload, remove, uploading, removingUploadIds, error };
}
