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
  facts?: Record<string, unknown>;
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
  from_route?: string | null;
  to_route?: string | null;
  doses_per_day?: number | null;
  suggested_daily_total?: string | null;
  label_max_daily?: string | null;
  ceiling_exceeded?: boolean;
  ceiling_citations?: string[];
}

export interface ScoreTerm {
  term: string;
  value: number;
  detail: string;
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
  score_breakdown?: ScoreTerm[];
  requires_specialist_review: boolean;
  manufacturers?: string[];
  routes_available?: string[];
  route_match?: boolean | null;
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
  renal?: RenalEstimate | null;
  warnings?: string[];
  timings_ms?: Record<string, number>;
  engine_version?: string;
}

export interface RenalEstimate {
  value_ml_min: number | null;
  source: string;
  method: string | null;
  weight_used: string | null;
  formula: string | null;
  egfr_ckd_epi_2021: number | null;
  warnings: string[];
}

export type Sex = "female" | "male" | "unknown";
export type CreatinineUnit = "mg/dL" | "umol/L";

export interface PatientContext {
  age_years?: number | null;
  weight_kg?: number | null;
  height_cm?: number | null;
  sex?: Sex;
  serum_creatinine?: number | null;
  serum_creatinine_unit?: CreatinineUnit;
  on_dialysis?: boolean;
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
  initial_posting_date?: string | null;
}

export interface Meta {
  version: string;
  corpus: Record<string, number>;
  data_versions: Record<string, string>;
  embedding_model: string;
  llm_provider: string;
  tiers: { id: Tier; label: string }[];
  gates: { gate: GateName; question: string; sections: string[] }[];
  store_mode?: string;
  runtime?: string;
  auth_required?: boolean;
  features?: string[];
}

export interface Ranked { name: string; count: number }

export interface ShortageBrief {
  record_id: string;
  generic_name: string;
  presentation: string | null;
  company_name: string | null;
  status: ShortageStatus;
  initial_posting_date: string | null;
  update_date: string | null;
  days_listed: number | null;
}

export interface Insights {
  generated_at: string;
  as_of: string;
  total_records: number;
  by_status: Record<string, number>;
  current: {
    entries: number;
    ingredients: number;
    companies: number;
    by_category: Ranked[];
    by_reason: Ranked[];
    by_route_family: Ranked[];
    top_companies: Ranked[];
    most_affected_ingredients: Ranked[];
    days_listed: { median: number | null; p90: number | null; max: number | null };
    longest_running: ShortageBrief[];
  };
  recently_updated: ShortageBrief[];
  timeline: { month: string; new_postings: number; updates: number }[];
}

export interface EquivalenceTable {
  id: string;
  type: "ratio" | "mme" | "band";
  title: string;
  unit: string;
  reference: string;
  reference_url: string;
  members: Record<string, Record<string, number | number[]>>;
  excluded: Record<string, string>;
  warnings: string[];
}

export type ReviewDecision = "agree" | "override" | "reject";

export interface ReviewInput {
  request_id: string;
  candidate_ingredient: string;
  candidate_tier?: Tier | null;
  decision: ReviewDecision;
  reason?: string | null;
  chosen_alternative?: string | null;
}

export interface ReviewRecord extends ReviewInput {
  review_id: string;
  created_at: string;
  rank: number | null;
}

export interface ReviewSummary {
  reviews: number;
  requests_reviewed: number;
  decisions: Record<string, number>;
  agreement_rate: number | null;
  top_option_agreement_rate: number | null;
  top_override_reasons: { reason: string; count: number }[];
}

export interface AuditVerification {
  ok: boolean;
  checked: number;
  legacy_unchained: number;
  head_hash: string | null;
  first_break: string | null;
  reason: string | null;
}

export interface BatchResponse {
  results: { label: string | null; recommendation: Recommendation }[];
  summary: Record<string, unknown>;
}
