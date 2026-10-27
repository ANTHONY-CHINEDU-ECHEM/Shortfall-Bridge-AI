import type { GateName, GateStatus, Tier } from "./types";

export const GATE_LABEL: Record<GateName, string> = {
  availability: "Supply",
  allergy: "Allergy",
  renal: "Renal",
  hepatic: "Hepatic",
  pregnancy_lactation: "Pregnancy",
  interactions: "Interactions",
  boxed_warning: "Boxed warning",
  age: "Age",
};

export const GATE_ORDER: GateName[] = [
  "availability",
  "allergy",
  "renal",
  "hepatic",
  "pregnancy_lactation",
  "interactions",
  "boxed_warning",
  "age",
];

export const STATUS_LABEL: Record<GateStatus, string> = {
  pass: "Clear",
  caution: "Caution",
  block: "Blocked",
  unknown: "Needs data",
};

export const TIER_LABEL: Record<Tier, string> = {
  T1_therapeutic_equivalent: "Equivalent product",
  T2_alternative_formulation: "Other formulation",
  T3_therapeutic_interchange: "Same class",
  T4_alternative_class: "Different class",
};

export const TIER_HELP: Record<Tier, string> = {
  T1_therapeutic_equivalent: "Same ingredient, form, route and strength from an unaffected manufacturer, A rated in the Orange Book.",
  T2_alternative_formulation: "Same ingredient by another dosage form or route.",
  T3_therapeutic_interchange: "Different ingredient in the same pharmacological class or published equivalence table.",
  T4_alternative_class: "Different class with overlapping labelled indications. Needs specialist review.",
};

export const SECTION_LABEL: Record<string, string> = {
  boxed_warning: "Boxed warning",
  indications_and_usage: "Indications and usage",
  dosage_and_administration: "Dosage and administration",
  dosage_forms_and_strengths: "Dosage forms and strengths",
  contraindications: "Contraindications",
  warnings_and_cautions: "Warnings and precautions",
  warnings: "Warnings",
  precautions: "Precautions",
  adverse_reactions: "Adverse reactions",
  drug_interactions: "Drug interactions",
  use_in_specific_populations: "Use in specific populations",
  pregnancy: "Pregnancy",
  lactation: "Lactation",
  pediatric_use: "Pediatric use",
  geriatric_use: "Geriatric use",
  clinical_pharmacology: "Clinical pharmacology",
  pharmacokinetics: "Pharmacokinetics",
  mechanism_of_action: "Mechanism of action",
  overdosage: "Overdosage",
  how_supplied: "How supplied",
  description: "Description",
};

export function titleCase(s: string): string {
  return s.replace(/\b([a-z])/g, (m) => m.toUpperCase());
}
