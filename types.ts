// Mirrors backend/shortfall_bridge/domain/models.py

export type GateStatus = "pass" | "caution" | "block" | "unknown";
export type GateName =
  | "availability"
  | "allergy"
  | "renal"
  | "hepatic"
  | "pregnancy_lactation"
  | "interactions"
  | "boxed_warning"
  | "age";
export type Tier =
  | "T1_therapeutic_equivalent"
  | "T2_alternative_formulation"
  | "T3_therapeutic_interchange"
  | "T4_alternative_class";
export type ShortageStatus = "Current" | "Resolved" | "To Be Discontinued" | "Unknown";
export type HepaticImpairment = "none" | "mild" | "moderate" | "severe";

export interface GateResult {
  gate: GateName;
  status: GateStatus;
  rationale: string;
  citations: string[];
}

export interface DoseConversion {
  feasible: boolean;
  from_drug: string;
  to_drug: string;
  from_dose: string | null;
  suggested_dose: string | null;
  suggested_range: string | null;
  method: string;
  reference: string | null;
  warnings: string[];
}

export interface Candidate {
  ingredient: string;
  display_name: string;
  tier: Tier;
  rationale: string;
  products: string[];
  te_codes: string[];
  shared_classes: string[];
  gates: GateResult[];
  dose_conversion: DoseConversion | null;
  citations: string[];
  score: number;
  requires_specialist_review: boolean;
}

export interface Citation {
  chunk_id: string;
  set_id: string;
  section: string;
  title: string;
  snippet: string;
  dailymed_url: string;
  effective_time: string | null;
}

export interface ShortageSummary {
  in_shortage: boolean;
  status: ShortageStatus;
  matching_records: number;
  presentations: string[];
  reasons: string[];
  availability_notes: string[];
  last_updated: string | null;
}

export interface NarrativeSentence {
  text: string;
  citations: string[];
  engine_facts: string[];
}

export interface Recommendation {
  request_id: string;
  created_at: string;
  status: "ok" | "partial" | "abstain";
  query: { display_name: string; ingredients: string[]; resolution_method: string; confidence: number } | null;
  shortage: ShortageSummary | null;
  candidates: Candidate[];
  not_recommended: Candidate[];
  narrative: { sentences: NarrativeSentence[]; generator: string; verification: Record<string, unknown> };
  citations: Record<string, Citation>;
  abstain_reasons: string[];
  data_versions: Record<string, string>;
  disclaimer: string;
  latency_ms: number;
}

export interface PatientContext {
  age_years?: number | null;
  weight_kg?: number | null;
  creatinine_clearance_ml_min?: number | null;
  egfr_ml_min_1_73m2?: number | null;
  hepatic_impairment: HepaticImpairment;
  pregnant: boolean;
  breastfeeding: boolean;
  allergies: string[];
  current_medications: string[];
}

export interface SubstitutionRequest {
  drug: string;
  route?: string | null;
  dosage_form?: string | null;
  strength?: string | null;
  current_dose?: { amount: number; unit: string; frequency?: string | null } | null;
  patient: PatientContext;
  clinical_question?: string | null;
  max_candidates: number;
  include_alternative_classes: boolean;
}

export interface DrugSuggestion {
  ingredient: string;
  display: string;
  score: number;
  in_current_shortage: boolean;
}

export interface ShortageRecord {
  record_id: string;
  generic_name: string;
  ingredients: string[];
  company_name: string | null;
  presentation: string | null;
  dosage_form: string | null;
  status: ShortageStatus;
  availability: string | null;
  shortage_reason: string | null;
  therapeutic_category: string[];
  routes: string[];
  update_date: string | null;
}

export interface Meta {
  version: string;
  corpus: Record<string, number>;
  data_versions: Record<string, string>;
  embedding_model: string;
  llm_provider: string;
  tiers: { id: Tier; label: string }[];
  gates: { gate: GateName; question: string; sections: string[] }[];
}
