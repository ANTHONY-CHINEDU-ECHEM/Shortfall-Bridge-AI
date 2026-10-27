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

## Verification

* Unit and integration tests cover every gate, the equivalence engine and the verifier.
* `shortfall evaluate` runs clinical scenarios with expected safety properties. A pharmacist must
  review and sign off the scenario expectations before results are used as a release gate.
* Each recommendation is stored in `recommendation_audit` with request, response and data versions.
