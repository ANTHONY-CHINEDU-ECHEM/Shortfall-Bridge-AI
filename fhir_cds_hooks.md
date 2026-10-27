# FHIR R4 and CDS Hooks integration

Version 2 lets an electronic prescribing system call Shortfall Bridge directly, in two ways.

## 1. FHIR substitution endpoint

`POST /api/v1/fhir/substitutions` accepts a FHIR R4 `Bundle` (any type), a single `MedicationRequest`,
or a CDS Hooks style `{"draftOrders": Bundle, "prefetch": {...}}`, and returns:

```json
{ "mapping": { "order_id": "mr1", "mapped": [ { "field": "serum_creatinine", "value": "150 umol/L",
               "source": "Observation 14682-9 2026-10-25" } ], "unmapped": [], "warnings": [] },
  "recommendation": { "...": "the normal Recommendation payload" } }
```

Mapping rules, all unit tested in `backend/tests/test_fhir_cds.py`:

| FHIR | Becomes | Notes |
|---|---|---|
| `MedicationRequest` (draft first, then active or on hold) | drug, RxCUI, route, dose, frequency | medication text, coding display, contained or referenced `Medication`; `Timing.repeat` becomes "twice daily" or "every 8 hours" |
| `Patient.birthDate`, `Patient.gender` | age, sex | sex is used only by the renal equations |
| `Observation` LOINC 2160-0, 38483-4, 14682-9 | serum creatinine | latest by date wins; umol/L recognised |
| `Observation` 33914-3, 48642-3, 48643-1, 62238-1, 98979-8, 88293-6, 88294-4 | eGFR | |
| `Observation` 2164-2 | creatinine clearance | |
| `Observation` 29463-7, 3141-9 and 8302-2, 8306-3 | weight, height | pounds and inches converted |
| `Observation` 82810-3 or `Condition` 77386006 | pregnant | |
| `Condition` 105502003 or ICD-10 Z99.2 | on dialysis | |
| `AllergyIntolerance` | allergies | refuted, entered in error, inactive and non medication entries are skipped |
| other active `MedicationRequest`, `MedicationStatement` | current medicines | the order being replaced is excluded |

Example: `docs/examples/fhir_bundle.json`, or `shortfall fhir docs/examples/fhir_bundle.json`.

## 2. CDS Hooks 2.0 service

Discovery at `GET /cds-services` publishes two services:

| Service id | Hook | When it fires |
|---|---|---|
| `shortfall-bridge-order-select` | `order-select` | while the prescriber chooses a medicine |
| `shortfall-bridge-order-sign` | `order-sign` | before the order is signed |

Both request prefetch templates for the patient, active medications, active allergies, recent renal
observations and body measurements, so the EHR sends everything in one call.

Behaviour:

* If the drafted drug is **not** in current FDA shortage the service returns `{"cards": []}`. Alert
  fatigue is a safety hazard, so silence is the default (`SFB_CDS_ALWAYS_CARD=true` changes that).
* If it **is** short, one card is returned: indicator `warning` (or `critical` when no safe alternative
  exists), a summary naming the top option and dose, a Markdown table of the top three options with
  their non clear checks, the not recommended list, and the audit reference.
* Up to two **suggestions** replace the draft: a `delete` of the draft `MedicationRequest` and a `create`
  of a draft order for the alternative at the converted dose, carrying the original subject, encounter
  and requester. `selectionBehavior` is `at-most-one`.
* With `SFB_PUBLIC_APP_URL` set, the card links to `/#/workbench?ref=<request id>`, which opens the
  stored recommendation with all label evidence.
* Override reasons "Local stock available" and "Clinical judgement" are offered for feedback.

Try it in the public CDS Hooks sandbox (https://sandbox.cds-hooks.org) by registering
`https://<your-api-host>/cds-services`, or post `docs/examples/cds_order_select.json`:

```bash
curl -s -X POST localhost:8000/cds-services/shortfall-bridge-order-select \
  -H 'Content-Type: application/json' -d @docs/examples/cds_order_select.json | jq '.cards[0].summary'
```

When `SFB_API_KEYS` is set the CDS routes require the key too (`Authorization: Bearer <key>`); configure
the EHR's CDS client accordingly, or terminate its JWT at your gateway.
