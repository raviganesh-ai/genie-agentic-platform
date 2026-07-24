/** Mirrors backend/app/services/architecture_service.py's local
 * ArchitectureComponent/ArchitectureSnapshot models 1:1 (these are
 * intentionally not in backend/app/models/ per that file's own docstring). */
import type { DecisionGraph } from "./collaboration";

export interface ArchitectureComponent {
  step_id: string;
  recommended_by: string;
  content: string;
}

export interface ArchitectureSnapshot {
  session_id: string;
  workflow_run_id: string;
  components: ArchitectureComponent[];
  decision_graph: DecisionGraph | null;
}
