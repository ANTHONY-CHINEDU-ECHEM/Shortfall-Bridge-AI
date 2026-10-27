"""Normalisation of drug names, routes, dosage forms and strengths across FDA sources.

The three FDA sources describe the same products with different conventions:

* openFDA labels:     substance_name "FUROSEMIDE", route "INTRAVENOUS"
* Orange Book:        ingredient "FUROSEMIDE", "INJECTABLE;INJECTION", strength "10MG/ML"
* Drug Shortages:     generic_name "Furosemide Injection", dosage_form "Injection, solution"

Everything here is deterministic and unit tested because matching errors propagate into
clinical recommendations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Salt and ester suffixes stripped to obtain the active moiety. Salts are kept on the ingredient
# itself because different salts can be different products (metoprolol tartrate versus succinate),
# but class lookups and dose equivalence work on the moiety.
_SALT_TOKENS = {
    "hydrochloride", "hcl", "dihydrochloride", "hydrobromide", "sodium", "potassium", "calcium",
    "magnesium", "sulfate", "sulphate", "besylate", "besilate", "maleate", "mesylate", "mesilate",
    "tartrate", "bitartrate", "succinate", "fumarate", "citrate", "acetate", "phosphate",
    "disodium", "dipotassium", "monohydrate", "dihydrate", "trihydrate", "anhydrous", "hyclate",
    "tosylate", "lactate", "gluconate", "bromide", "chloride", "nitrate", "pamoate", "valerate",
    "propionate", "dipropionate", "furoate", "xinafoate", "trometamol", "tromethamine", "meglumine",
    "sodium succinate", "sodium phosphate", "hemihydrate", "erbumine", "olamine", "decanoate",
}

_MULTI_SPLIT = re.compile(r"\s*(?:;|,|\band\b|\s/\s|\+)\s*", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9 .\-]")
_WS = re.compile(r"\s+")

# Words that appear in shortage generic_name fields but are not part of the ingredient.
_FORM_WORDS = {
    "injection", "injectable", "tablet", "tablets", "capsule", "capsules", "oral", "solution",
    "suspension", "for", "usp", "extended", "release", "delayed", "er", "xr", "sr", "dr", "ir",
    "film", "coated", "chewable", "orally", "disintegrating", "powder", "lyophilized", "single",
    "dose", "vial", "vials", "premix", "premixed", "in", "emulsion",
    "cream", "ointment", "gel", "patch", "transdermal", "inhalation", "aerosol", "nasal", "spray",
    "ophthalmic", "otic", "drops", "syrup", "elixir", "concentrate", "kit", "liquid", "intravenous",
    "subcutaneous", "intramuscular", "infusion", "pen", "prefilled", "syringe", "auto-injector",
    # strength and pack units, so "Furosemide 10 mg/mL injection" resolves to furosemide
    "mg", "mcg", "ug", "g", "gm", "ml", "l", "meq", "mmol", "unit", "units", "iu", "ampule", "ampoule",
    "bag", "bottle", "carton", "pack", "each", "per", "x",
}


def clean_text(value: str) -> str:
    value = value.lower().replace(" ", " ")
    value = _NON_ALNUM.sub(" ", value)
    return _WS.sub(" ", value).strip()


def normalize_ingredient(name: str) -> str:
    """Canonical lower case ingredient name with salt retained."""
    return clean_text(name).strip(" .-")


def active_moiety(name: str) -> str:
    """Strip salt, ester and hydrate tokens to the active moiety."""
    tokens = normalize_ingredient(name).split(" ")
    while len(tokens) > 1 and tokens[-1] in _SALT_TOKENS:
        tokens.pop()
    # Leading salt tokens occur in a few names ("sodium valproate" style in UK naming).
    while len(tokens) > 1 and tokens[0] in {"sodium", "potassium", "calcium", "magnesium"}:
        tokens.pop(0)
    return " ".join(tokens)


def split_ingredients(name: str) -> list[str]:
    """Split a multi ingredient name into normalised ingredients."""
    parts = [normalize_ingredient(p) for p in _MULTI_SPLIT.split(name or "") if p and p.strip()]
    out: list[str] = []
    for p in parts:
        if p and p not in out:
            out.append(p)
    return out


def ingredients_from_shortage_name(generic_name: str) -> list[str]:
    """Shortage entries use descriptive names such as 'Amoxicillin Oral Powder for Suspension'."""
    base = clean_text(generic_name)
    base = re.sub(r"\(.*?\)", " ", base)
    words = [w for w in base.split(" ") if w and w not in _FORM_WORDS and not re.match(r"^\d", w)]
    joined = " ".join(words)
    return [active_moiety(p) for p in split_ingredients(joined) if p]


# Routes ---------------------------------------------------------------------------------------

ROUTE_FAMILIES: dict[str, str] = {
    "oral": "oral",
    "by mouth": "oral",
    "po": "oral",
    "enteral": "oral",
    "sublingual": "oromucosal",
    "buccal": "oromucosal",
    "intravenous": "parenteral",
    "iv": "parenteral",
    "intramuscular": "parenteral",
    "im": "parenteral",
    "subcutaneous": "parenteral",
    "sc": "parenteral",
    "injection": "parenteral",
    "injectable": "parenteral",
    "intrathecal": "neuraxial",
    "epidural": "neuraxial",
    "intravitreal": "ophthalmic",
    "ophthalmic": "ophthalmic",
    "otic": "otic",
    "nasal": "nasal",
    "intranasal": "nasal",
    "inhalation": "inhalation",
    "respiratory (inhalation)": "inhalation",
    "topical": "topical",
    "cutaneous": "topical",
    "transdermal": "transdermal",
    "rectal": "rectal",
    "vaginal": "vaginal",
}


def normalize_route(route: str | None) -> str | None:
    if not route:
        return None
    r = clean_text(route)
    if r in ROUTE_FAMILIES:
        return r if r not in {"iv", "im", "sc", "po", "by mouth", "injectable"} else {
            "iv": "intravenous", "im": "intramuscular", "sc": "subcutaneous",
            "po": "oral", "by mouth": "oral", "injectable": "injection",
        }[r]
    for key in ROUTE_FAMILIES:
        if key in r:
            return key
    return r


def route_family(route: str | None) -> str | None:
    r = normalize_route(route)
    if r is None:
        return None
    return ROUTE_FAMILIES.get(r, r)


# Dosage forms ---------------------------------------------------------------------------------

_MODIFIED_RELEASE = re.compile(r"extended|delayed|sustained|controlled|modified|\ber\b|\bxr\b|\bsr\b|\bdr\b|\bxl\b|\bla\b")


@dataclass(frozen=True)
class DosageForm:
    base: str
    modified_release: bool

    def __str__(self) -> str:
        return f"{self.base}{' (modified release)' if self.modified_release else ''}"


def normalize_dosage_form(form: str | None) -> DosageForm | None:
    if not form:
        return None
    f = clean_text(form.replace(";", " "))
    mr = bool(_MODIFIED_RELEASE.search(f))
    if any(k in f for k in ("inject", "infusion", "vial", "syringe")):
        base = "injection"
    elif "tablet" in f:
        base = "tablet"
    elif "capsule" in f:
        base = "capsule"
    elif "suspension" in f or "for suspension" in f:
        base = "suspension"
    elif "solution" in f or "syrup" in f or "elixir" in f or "liquid" in f:
        base = "solution"
    elif "patch" in f or ("film" in f and "transdermal" in f):
        base = "patch"
    elif "inhal" in f or "aerosol" in f:
        base = "inhalation"
    elif "cream" in f or "ointment" in f or "gel" in f or "lotion" in f:
        base = "topical"
    elif "suppositor" in f:
        base = "suppository"
    elif "powder" in f:
        base = "powder"
    else:
        base = f.split(" ")[0] if f else "unknown"
    return DosageForm(base=base, modified_release=mr)


def split_orange_book_form_route(df_route: str) -> tuple[str, str]:
    """Orange Book encodes 'DOSAGE FORM;ROUTE', e.g. 'TABLET, FILM COATED;ORAL'."""
    if ";" in df_route:
        form, route = df_route.split(";", 1)
        return form.strip(), route.strip()
    return df_route.strip(), ""


# Strengths ------------------------------------------------------------------------------------

_UNIT_ALIASES = {
    "mg": "mg", "milligram": "mg", "milligrams": "mg",
    "g": "g", "gm": "g", "gram": "g", "grams": "g",
    "mcg": "mcg", "ug": "mcg", "µg": "mcg", "microgram": "mcg", "micrograms": "mcg",
    "ml": "ml", "milliliter": "ml", "millilitre": "ml",
    "l": "l", "unit": "unit", "units": "unit", "iu": "unit", "meq": "meq", "mmol": "mmol",
    "%": "%", "hr": "hr", "h": "hr", "actuation": "actuation", "spray": "actuation",
}
_TO_MG = {"g": 1000.0, "mg": 1.0, "mcg": 0.001}
_STRENGTH_RE = re.compile(
    r"(?P<val>\d+(?:\.\d+)?)\s*(?P<unit>mg|mcg|ug|µg|g|gm|units?|iu|meq|mmol|%)"
    r"(?:\s*/\s*(?P<per_val>\d+(?:\.\d+)?)?\s*(?P<per_unit>ml|l|hr|h|actuation|spray|g|gm|mg)?)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Strength:
    value: float
    unit: str
    per_value: float | None = None
    per_unit: str | None = None

    def canonical(self) -> tuple[float, str, float | None, str | None]:
        """Canonical tuple for equality: mass in mg, concentration per 1 unit of volume."""
        value, unit = self.value, self.unit
        if unit in _TO_MG:
            value, unit = value * _TO_MG[unit], "mg"
        per_value = self.per_value
        if self.per_unit and per_value:
            value, per_value = value / per_value, 1.0
        return (round(value, 6), unit, per_value, self.per_unit)

    def __str__(self) -> str:
        s = f"{self.value:g} {self.unit}"
        if self.per_unit:
            s += f"/{'' if not self.per_value or self.per_value == 1 else f'{self.per_value:g} '}{self.per_unit}"
        return s


def parse_strength(text: str | None) -> Strength | None:
    if not text:
        return None
    m = _STRENGTH_RE.search(text.replace(",", ""))
    if not m:
        return None
    unit = _UNIT_ALIASES.get(m.group("unit").lower(), m.group("unit").lower())
    per_unit = m.group("per_unit")
    per_unit_n = _UNIT_ALIASES.get(per_unit.lower(), per_unit.lower()) if per_unit else None
    per_val = float(m.group("per_val")) if m.group("per_val") else (1.0 if per_unit_n else None)
    return Strength(float(m.group("val")), unit, per_val, per_unit_n)


def same_strength(a: str | None, b: str | None) -> bool:
    sa, sb = parse_strength(a), parse_strength(b)
    if sa is None or sb is None:
        return False
    return sa.canonical() == sb.canonical()


def word_boundary_contains(haystack: str, needle: str, plural: bool = False) -> bool:
    """Whole word match. ``plural`` also accepts a trailing s or es ("sulfonamide" in "sulfonamides")."""
    if not needle or len(needle) < 3:
        return False
    suffix = "(?:e?s)?" if plural else ""
    return re.search(rf"(?<![a-z0-9]){re.escape(needle.lower())}{suffix}(?![a-z0-9])", haystack.lower()) is not None


_TEXT_ROUTE_HINTS: tuple[tuple[str, str], ...] = (
    ("inject", "parenteral"), ("infusion", "parenteral"), ("intravenous", "parenteral"), ("vial", "parenteral"),
    ("syringe", "parenteral"), ("intramuscular", "parenteral"), ("subcutaneous", "parenteral"),
    ("ophthalmic", "ophthalmic"), ("otic", "otic"), ("nasal", "nasal"), ("inhal", "inhalation"),
    ("nebuli", "inhalation"), ("transdermal", "transdermal"), ("topical", "topical"), ("cream", "topical"),
    ("ointment", "topical"), ("rectal", "rectal"), ("suppositor", "rectal"), ("vaginal", "vaginal"),
    ("oral", "oral"), ("tablet", "oral"), ("capsule", "oral"), ("syrup", "oral"), ("elixir", "oral"),
    ("for suspension", "oral"),
)


def text_route_families(*texts: str | None) -> set[str]:
    """Route families implied by free text such as a shortage presentation ('Injection, solution')."""
    joined = " ".join(t for t in texts if t).lower()
    return {fam for hint, fam in _TEXT_ROUTE_HINTS if hint in joined}
