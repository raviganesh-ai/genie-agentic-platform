/** Mirrors backend/app/models/requirements_qualification.py's
 * RequirementsQualification model 1:1 (unlike ArchitectureSnapshot, this one
 * IS a real backend/app/models/ model, not a service-local one). */
export type RequirementsQualificationStatus =
  | "pending"
  | "qualified"
  | "not_qualified"
  | "undetermined";

export interface RequirementsQualification {
  status: RequirementsQualificationStatus;
  reason: string | null;
  assessed_by_agent_id: string | null;
}
