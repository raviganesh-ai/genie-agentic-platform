/** Mirrors backend/app/models/session_models.py 1:1. */

export type SessionStatus = "created" | "active" | "completed" | "archived";

export interface Session {
  id: string;
  owner_user_id: string;
  title: string;
  status: SessionStatus;
  created_at: string;
  updated_at: string;
  latest_workflow_run_id: string | null;
}
