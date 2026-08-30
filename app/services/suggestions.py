"""Smart category and priority suggestions (Phase 14).

DESIGN RULE -- READ BEFORE EXTENDING
------------------------------------
This module **only ever suggests**. It never writes to a complaint. The value a
student picks, and the value an admin later confirms, are the only things
stored. A suggestion is surfaced in the UI as "Did you mean ...?" with the
student free to ignore it entirely.

That rule comes straight from the specification: an uploaded photo is
*evidence*, and must not automatically determine a complaint's category or
priority without validation. The same restraint applies to the text analysis
here -- a misfiled complaint that a human chose is far less damaging than one
silently reclassified by a keyword match.

If image analysis is added later (see ``docs/ROADMAP.md``), it must plug in
through :func:`suggest` and return a ``Suggestion`` like everything else -- a
proposal for a person to accept or reject, never an automatic classification.

The implementation is deliberately transparent keyword scoring rather than a
trained model: it needs no dataset, runs offline in microseconds, and every
suggestion can be explained to the user ("matched: leaking, water").
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..constants import Priority

#: Category name -> keywords that indicate it. Category names here must match
#: the seeded categories in ``scripts/seed.py``.
CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "Classroom Equipment": (
        "projector",
        "screen",
        "hdmi",
        "podium",
        "speaker",
        "microphone",
        "mic",
        "blackboard",
        "whiteboard",
        "marker",
        "bench",
        "desk",
        "chair",
    ),
    "Electrical": (
        "electric",
        "electrical",
        "power",
        "socket",
        "plug",
        "switch",
        "fan",
        "light",
        "bulb",
        "tube",
        "wiring",
        "short circuit",
        "shock",
        "fuse",
        "mcb",
    ),
    "Plumbing & Water": (
        "water",
        "leak",
        "leaking",
        "leakage",
        "tap",
        "pipe",
        "drain",
        "drainage",
        "flush",
        "overflow",
        "seepage",
        "cooler",
        "dripping",
    ),
    "Washroom & Sanitation": (
        "washroom",
        "toilet",
        "restroom",
        "bathroom",
        "urinal",
        "soap",
        "dirty",
        "unhygienic",
        "smell",
        "stink",
        "garbage",
        "dustbin",
        "cleaning",
    ),
    "Wi-Fi & Network": (
        "wifi",
        "wi-fi",
        "internet",
        "network",
        "router",
        "lan",
        "ethernet",
        "connectivity",
        "slow speed",
        "disconnect",
    ),
    "Furniture": ("furniture", "table", "cupboard", "almirah", "broken chair", "stool"),
    "Air Conditioning": ("ac", "air conditioner", "cooling", "hvac", "thermostat"),
    "Civil & Building": (
        "ceiling",
        "wall",
        "floor",
        "tile",
        "crack",
        "paint",
        "door",
        "window",
        "glass",
        "roof",
    ),
    "Computer Lab": (
        "computer",
        "pc",
        "monitor",
        "keyboard",
        "mouse",
        "cpu",
        "lab system",
        "software",
        "printer",
    ),
}

#: Words that raise urgency, with the priority they argue for.
PRIORITY_KEYWORDS: dict[str, tuple[str, ...]] = {
    Priority.URGENT: (
        "shock",
        "fire",
        "smoke",
        "spark",
        "sparking",
        "short circuit",
        "injury",
        "injured",
        "danger",
        "dangerous",
        "emergency",
        "gas leak",
        "collapse",
        "flooding",
        "electrocuted",
        "electrocution",
    ),
    Priority.HIGH: (
        "leaking",
        "leakage",
        "no power",
        "no water",
        "not working",
        "broken",
        "exam",
        "unusable",
        "overflow",
        "blocked",
        "stuck",
    ),
    Priority.LOW: (
        "minor",
        "cosmetic",
        "slightly",
        "whenever possible",
        "suggestion",
        "request",
        "repaint",
    ),
}


@dataclass
class Suggestion:
    """A proposal for a human to accept or ignore."""

    value: str | None = None
    confidence: float = 0.0
    matched: list[str] = field(default_factory=list)

    @property
    def is_useful(self) -> bool:
        """Only surface a suggestion the engine is reasonably sure about."""
        return self.value is not None and self.confidence >= 0.3

    @property
    def reason(self) -> str:
        return "matched: " + ", ".join(self.matched[:4]) if self.matched else ""


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower())


def _score(haystack: str, keywords: tuple[str, ...]) -> list[str]:
    """Return the keywords present in ``haystack``.

    Matching is anchored at both ends of the word, with a small set of English
    inflections allowed at the end so "leak" also finds "leaks", "leaked" and
    "leaking". Without the trailing boundary a keyword would match any word
    merely starting with it -- "plug" would match "plughole".
    """
    hits = []
    for keyword in keywords:
        pattern = re.escape(keyword.strip())
        if re.search(rf"\b{pattern}(?:s|es|ed|ing)?\b", haystack):
            hits.append(keyword.strip())
    return hits


def suggest_category(title: str, description: str) -> Suggestion:
    """Propose a category from the complaint's wording."""
    haystack = _normalise(f"{title} {description}")
    if not haystack.strip():
        return Suggestion()

    best = Suggestion()
    for category, keywords in CATEGORY_KEYWORDS.items():
        if not keywords:
            continue
        hits = _score(haystack, keywords)
        if not hits:
            continue
        # Two distinct hits is already convincing; more adds diminishing value.
        confidence = min(1.0, 0.35 + 0.25 * len(hits))
        if confidence > best.confidence:
            best = Suggestion(value=category, confidence=confidence, matched=hits)
    return best


def suggest_priority(title: str, description: str) -> Suggestion:
    """Propose a priority, biased towards safety-critical wording."""
    haystack = _normalise(f"{title} {description}")
    if not haystack.strip():
        return Suggestion()

    # Checked most-severe first: one mention of "sparking" outweighs any number
    # of low-priority words.
    for priority in (Priority.URGENT, Priority.HIGH, Priority.LOW):
        hits = _score(haystack, PRIORITY_KEYWORDS[priority])
        if hits:
            confidence = min(1.0, 0.45 + 0.2 * len(hits))
            return Suggestion(value=priority, confidence=confidence, matched=hits)

    return Suggestion(value=Priority.MEDIUM, confidence=0.3, matched=[])


def suggest(title: str, description: str) -> dict[str, Suggestion]:
    """Both suggestions for a complaint, ready to render as hints."""
    return {
        "category": suggest_category(title, description),
        "priority": suggest_priority(title, description),
    }
