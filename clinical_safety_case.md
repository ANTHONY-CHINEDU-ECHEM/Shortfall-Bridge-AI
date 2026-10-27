# Clinical safety case and hazard log

This document follows the structure of NHS Digital's DCB0129 (clinical risk management for the
manufacture of health IT systems). It is a starting hazard log for engineering review. A named
Clinical Safety Officer must own, extend and sign it before any clinical deployment.

## Intended use

Decision support for pharmacists and prescribers who must replace a medicine affected by a
supply shortage. The system proposes options, shows safety findings with the label text that
supports them, and suggests starting doses where a published equivalence table applies. It does
not prescribe, dispense, order or administer. A qualified clinician makes and documents every
decision.

## Out of scope

Paediatric weight based dosing calculations, neonatal care, chemotherapy regimen substitution,
antimicrobial selection without microbiology, and any use outside the clinician's own
professional judgement.

## Risk matrix

Severity: 1 minor, 2 significant, 3 considerable, 4 major, 5 catastrophic.
Likelihood: 1 very low, 2 low, 3 medium, 4 high, 5 very high.
Risk is rated after controls.

## Hazards

| ID | Hazard | Cause | Effect | Controls in the system | Sev | Lik | Residual |
|---|---|---|---|---|---|---|---|
| H01 | Contraindicated drug proposed | Interaction or contraindication not detected | Patient harm | Gates read whole sections, not top k retrieval; both labels scanned in both directions; class and CYP aliases; blocked options moved out of the ranked list | 4 | 2 | Medium |
| H02 | Wrong equivalent dose | Table error, unit or route mismatch | Under or overdose | Published tables with references; unit check; routes not covered are refused; opioid cross tolerance reduction; methadone, fentanyl and buprenorphine always refused; rounding to measurable doses with exact value shown | 4 | 2 | Medium |
| H03 | Hallucinated statement in summary | Language model invents a drug, number or fact | Clinician misled | Model only narrates engine facts; verifier rejects sentences without valid citations, with unsupported numbers or unknown drugs; template fallback below 60 percent acceptance | 3 | 1 | Low |
| H04 | Missing patient data treated as safe | Renal function not entered | Inappropriate dose | Gates return Needs data rather than Clear when the label has relevant rules | 3 | 2 | Low |
| H05 | Stale shortage status | Ingestion job not run | Recommending a drug that is also short | Data freshness shown on every response and on the Method page; daily refresh job; supply gate | 2 | 3 | Medium |
| H06 | Stale label | openFDA partition lag | Out of date warning | Effective date shown per citation; DailyMed link to the live label; DailyMed refresh path | 3 | 2 | Medium |
| H07 | Wrong drug resolved | Fuzzy match on a look alike name | Alternatives for the wrong drug | Resolution confidence and method returned; result heading states the resolved ingredient; abstain below threshold | 4 | 1 | Low |
| H08 | Over reliance | Automation bias | Reduced checking | Acknowledgement required before copying a note; disclaimer on every result; evidence one click away | 3 | 3 | Medium |
| H09 | US labelling applied in another jurisdiction | Deployment outside the US | Mismatch with local licensing | Stated in the UI and README; UK adaptation path documented (dm+d, SmPC, BNF) | 3 | 3 | Medium |

| H10 | Wrong estimated renal function | Creatinine unit confusion, obesity, low muscle mass | Wrong renal decision | Unit selected explicitly; values above 30 mg/dL refused as probable umol/L; ideal or adjusted body weight with the weight used shown; unknown sex uses the lower estimate; elderly low creatinine and over 90 warnings; formula printed with every result; a measured value always overrides | 4 | 2 | Medium |
| H11 | Converted dose above the label maximum | Conversion multiplied by frequency exceeds a daily ceiling | Overdose | Daily total computed from frequency; labelled maximum daily dose extracted from Dosage and Administration and compared; ceiling exceedance flagged, cited, penalised in ranking and stated in the summary; ambiguous frequencies (as needed) say the check could not run | 4 | 2 | Medium |
| H12 | Alternative ranked first that cannot be given by the needed route | v1 scored route mismatched alternatives like matched ones | IV patient offered an oral only drug first | Route availability recorded per option; route mismatch penalty; conversion targets the available route with a route change warning; "Route change" tag in the interface | 3 | 2 | Low |
| H13 | Incomplete live data presented as complete | openFDA outage or rate limit in live or demo mode | Missing option or missing contraindication | Every failed live request is logged; the response downgrades to Partial with a warning naming the failed sources; data mode and freshness shown in every response and in the interface banner | 4 | 2 | Medium |
| H14 | Tampering with stored recommendations | Database edit after the fact | Audit trail misleads a review or investigation | Hash chained audit records (SHA-256 over content and previous hash); verification endpoint, CLI and interface; exported notes carry the record hash | 3 | 1 | Low |
| H15 | Duplicate therapy after substitution | Patient already takes a drug of the alternative's class | Additive toxicity | Same EPC class or equivalence table membership flagged as duplicate therapy in the interaction gate; the drug being replaced is excluded so it never flags its own alternatives | 3 | 2 | Low |
| H16 | Wrong order context from an EHR | FHIR mapping picks a stale value or wrong unit | Wrong patient context | Latest observation by date wins; units converted explicitly (lb, in, umol/L); refuted and inactive allergies skipped; every mapped value returned with its source resource, and unmapped items listed | 3 | 2 | Low |

## Verification

* Unit and integration tests cover every gate, the equivalence engine and the verifier.
* `shortfall evaluate` runs clinical scenarios with expected safety properties. A pharmacist must
  review and sign off the scenario expectations before results are used as a release gate.
* Each recommendation is stored in `recommendation_audit` with request, response and data versions,
  chained by SHA-256 hashes; `shortfall audit-verify` (or `GET /api/v1/audit/verify`) proves integrity.
* Pharmacist agreement and override reasons (`/api/v1/reviews`) are the field evidence for ranking
  quality; review `GET /api/v1/reviews/summary` at every safety review.
* v2 hazards H10 to H16 each have scenario or unit test coverage (see UPGRADES.md, Verification).
