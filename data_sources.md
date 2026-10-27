# Data sources

All sources are public and free to use. No synthetic data is used by the system. The files in
`backend/tests/fixtures` are miniature, openFDA shaped records used only by unit tests.

| Source | Publisher | Access | What it provides | Refresh |
|---|---|---|---|---|
| openFDA drug labels | US FDA | Bulk JSON partitions listed at https://api.fda.gov/download.json | Every structured product label on DailyMed, pre sectioned, with harmonised RxCUI, UNII, substance names and EPC classes | Weekly |
| FDA Drug Shortages | US FDA CDER | https://api.fda.gov/drug/shortages.json | Presentation level shortage entries: status, reason, availability, manufacturer, dates | Daily |
| Orange Book | US FDA | https://www.fda.gov/drugs/drug-approvals-and-databases/orange-book-data-files | Approved products with therapeutic equivalence codes (tilde delimited products.txt) | Monthly |
| RxNorm and RxClass | US National Library of Medicine | https://rxnav.nlm.nih.gov/REST | Ingredient resolution, ATC and DailyMed EPC class membership | Monthly |
| DailyMed SPL | US National Library of Medicine | https://dailymed.nlm.nih.gov/dailymed/spl-resources-all-drug-labels.cfm and the v2 web services | Authoritative SPL XML for audit and between release refresh | Daily |
| Dose equivalence tables | CDC, NICE, AHA/ACC/HFSA, peer reviewed literature | `backend/shortfall_bridge/engine/data/equivalence_tables.yaml` | Six published conversion references, each cited | On guideline change |

## Licensing

openFDA and FDA data are public domain US Government works. RxNorm content used through RxNav
is subject to the UMLS licence terms for some source vocabularies; this system only uses RxNorm
and RxClass outputs that NLM publishes openly. DailyMed content is public. Review the terms of
each source before redistribution.

## UK adaptation

For NHS use, replace or supplement:

* labels with the electronic Medicines Compendium (SmPC) or the MHRA product information,
* the product dictionary with NHS dm+d through NHS TRUD,
* the shortage feed with the DHSC Medicines Supply Tool and Specialist Pharmacy Service shortage pages,
* equivalence tables with BNF or local formulary conversion guidance.

The store and engine are source agnostic: only the adapters in `backend/shortfall_bridge/sources` change.
