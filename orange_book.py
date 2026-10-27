"""FDA Orange Book (Approved Drug Products with Therapeutic Equivalence Evaluations).

The compressed data file unzips into products.txt, patent.txt and exclusivity.txt, tilde
delimited ASCII. products.txt carries the therapeutic equivalence (TE) code that tells us which
multisource products FDA considers substitutable: codes beginning with "A" are therapeutically
equivalent, codes beginning with "B" are not.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from collections.abc import Iterator
from pathlib import Path

from ..config import Settings
from ..domain.models import OrangeBookProduct
from ..domain.normalize import active_moiety, split_ingredients, split_orange_book_form_route
from .http import HttpClient

logger = logging.getLogger(__name__)

# Header names have varied slightly between releases; map all known spellings.
_COLUMN_ALIASES = {
    "ingredient": "ingredient",
    "df;route": "df_route",
    "trade_name": "trade_name",
    "applicant": "applicant",
    "strength": "strength",
    "appl_type": "appl_type",
    "appl_no": "appl_no",
    "product_no": "product_no",
    "te_code": "te_code",
    "approval_date": "approval_date",
    "rld": "rld",
    "rs": "rs",
    "type": "type",
    "applicant_full_name": "applicant_full_name",
}


def download_orange_book(client: HttpClient, settings: Settings, dest_dir: Path) -> Path:
    path, _ = client.download(settings.orange_book_url, dest_dir / "orange_book.zip", overwrite=True)
    return path


def _products_text(source: Path) -> str:
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as zf:
            name = next((n for n in zf.namelist() if n.lower().endswith("products.txt")), None)
            if not name:
                raise FileNotFoundError("products.txt not found in Orange Book archive")
            return zf.read(name).decode("latin-1")
    return source.read_text(encoding="latin-1")


def iter_products(source: Path) -> Iterator[OrangeBookProduct]:
    text = _products_text(source)
    reader = csv.reader(io.StringIO(text), delimiter="~")
    header = next(reader)
    cols = [_COLUMN_ALIASES.get(h.strip().lower(), h.strip().lower()) for h in header]
    for row in reader:
        if not row or len(row) < len(cols) - 1:
            continue
        rec = dict(zip(cols, (c.strip() for c in row), strict=False))
        form, route = split_orange_book_form_route(rec.get("df_route", ""))
        ingredient = rec.get("ingredient", "")
        yield OrangeBookProduct(
            ingredient=ingredient,
            ingredients=[active_moiety(i) for i in split_ingredients(ingredient)],
            dosage_form=form,
            route=route,
            trade_name=rec.get("trade_name", ""),
            applicant=rec.get("applicant", ""),
            applicant_full_name=rec.get("applicant_full_name") or None,
            strength=rec.get("strength", ""),
            appl_type=rec.get("appl_type", ""),
            appl_no=rec.get("appl_no", ""),
            product_no=rec.get("product_no", ""),
            te_code=rec.get("te_code") or None,
            approval_date=rec.get("approval_date") or None,
            rld=rec.get("rld", "").lower() == "yes",
            rs=rec.get("rs", "").lower() == "yes",
            marketing_type=rec.get("type", ""),
        )
