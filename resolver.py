"""Resolve a clinician's free text drug name to indexed ingredients."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ..domain.models import DrugConcept
from ..domain.normalize import active_moiety, clean_text, ingredients_from_shortage_name
from ..store.base import KnowledgeStore

if TYPE_CHECKING:
    from ..sources.rxnav import RxNavClient

logger = logging.getLogger(__name__)


class DrugResolver:
    def __init__(self, store: KnowledgeStore, rxnav: RxNavClient | None = None, min_score: float = 0.74) -> None:
        self.store, self.rxnav, self.min_score = store, rxnav, min_score

    def resolve(self, text: str, rxcui: str | None = None) -> DrugConcept | None:
        query = clean_text(text)
        candidates = [query, *ingredients_from_shortage_name(query)]
        for term in candidates:
            hits = self.store.search_ingredients(term, limit=3)
            if hits and hits[0][2] >= self.min_score:
                ing, display, score = hits[0]
                return self._concept(text, ing, display, "local_index", score, rxcui)
        if self.rxnav is not None:
            try:
                _, name, score = self.rxnav.ingredient_for_term(text)
            except Exception as exc:  # RxNav outages must not take the engine down
                logger.warning("rxnav_resolution_failed: %s", exc)
                name, score = None, 0.0
            if name:
                moiety = active_moiety(name)
                if moiety in self.store.known_ingredients():
                    return self._concept(text, moiety, moiety, "rxnav_approximate", min(1.0, score / 100), rxcui)
        return None

    def _concept(
        self, query: str, ingredient: str, display: str, method: str, score: float, rxcui: str | None
    ) -> DrugConcept:
        labels = self.store.labels_for_ingredient(ingredient, limit=20)
        return DrugConcept(
            query=query,
            ingredients=[ingredient],
            display_name=display,
            rxcui=rxcui or next((r for d in labels for r in d.rxcuis), None),
            classes=self.store.classes_for(ingredient),
            label_set_ids=[d.set_id for d in labels],
            resolution_method=method,
            confidence=round(score, 3),
        )
