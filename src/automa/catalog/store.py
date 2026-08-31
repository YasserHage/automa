"""Reading, writing and merging the per-game card catalog.

One JSON file per game, holding what has been learned about its cards so far. It is
append-mostly and survives between matches, which is the entire point: the second match
of a game asks fewer questions than the first because this file remembers the answers.

Two properties matter more than they look:

* **Merging never loses an entry.** A catalog is knowledge accumulated by a person
  answering questions one at a time; silently dropping some of it would make them answer
  twice and would corrupt the "fewer descriptions than the first" measurement the exit
  criterion depends on.
* **Every entry is validated on the way in and on the way out.** An entry that does not
  match the vocabulary is refused rather than stored, and a stored entry that no longer
  validates - because the vocabulary changed under it - is reported rather than executed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from automa.catalog.vocabulary import unsupported, validate

#: Where catalogs live by default. Committed, because a populated catalog is evidence of
#: what the system learned, and a judge needs it to reproduce a second match.
CATALOG_DIR = Path("catalog")

SCHEMA_VERSION = 1


@dataclass
class CardEntry:
    """One card type, and what it was learned to do."""

    card_type: str
    effects: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    #: Set when a description could not be expressed in the vocabulary. The card is
    #: parked: known to exist, known to be un-modelled, and never silently treated as
    #: having no effect.
    unresolved: str = ""
    learned_at: str = ""

    @property
    def playable(self) -> bool:
        return bool(self.effects) and not self.unresolved

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "effects": self.effects,
            "description": self.description,
            "learned_at": self.learned_at,
        }
        if self.unresolved:
            data["unresolved"] = self.unresolved
        return data


@dataclass
class Catalog:
    """Everything known about one game's cards."""

    game: str
    entries: dict[str, CardEntry] = field(default_factory=dict)
    path: Path | None = None

    def __contains__(self, card_type: str) -> bool:
        return card_type in self.entries

    def __len__(self) -> int:
        return len(self.entries)

    def known(self) -> set[str]:
        """Card types with a usable effect. Parked cards are deliberately excluded."""
        return {k for k, e in self.entries.items() if e.playable}

    def parked(self) -> set[str]:
        """Card types described but not expressible - the escape case."""
        return {k for k, e in self.entries.items() if e.unresolved}

    def unknown_among(self, card_types: set[str]) -> set[str]:
        """Which of these still need describing.

        A parked card counts as *not* needing describing again: the person already
        answered, and the vocabulary could not hold it. Asking a second time would be
        rude, and it would corrupt the "fewer questions" measurement.
        """
        return {c for c in card_types if c not in self.entries}

    def effects_for(self, card_type: str) -> list[dict[str, Any]]:
        entry = self.entries.get(card_type)
        return list(entry.effects) if entry and entry.playable else []

    def described(self) -> set[str]:
        """Every card type someone has already been asked about - known *or* parked.

        The "do not ask again" set, and deliberately not the same as
        :meth:`known`. A parked card has no executable effect, but the person has
        already answered for it; asking again would waste their time and would inflate
        the question count the exit criterion compares between matches.
        """
        return set(self.entries)

    def as_state_data(self) -> dict[str, Any]:
        """The catalog in the shape a game module receives it.

        Both halves are needed and they are different questions. ``effects`` is what a
        module *executes*; ``described`` is what it must not *ask about again*. A module
        given only the first would re-ask every parked card on every match.

        Plain JSON-round-trippable data, so it can live inside a state without tripping
        the harness's serialisation check.
        """
        return {
            "effects": {k: list(e.effects) for k, e in self.entries.items() if e.playable},
            "described": sorted(self.entries),
        }

    # -- learning ---------------------------------------------------------

    def learn(
        self,
        card_type: str,
        effects: list[dict[str, Any]],
        description: str = "",
        supported: set[str] | None = None,
        terms: dict[str, set[str]] | None = None,
    ) -> list[str]:
        """Record what a card does. Returns validation problems; empty means accepted.

        Refuses rather than stores on invalid input. A catalog holding an unexecutable
        entry is worse than one that admits it does not know - and an entry using an
        operation *this* game does not implement is unexecutable in exactly the same
        way, so ``supported`` is checked alongside the vocabulary itself.
        """
        problems = validate(effects) + unsupported(effects, supported, terms)
        if problems:
            return problems
        self.entries[card_type] = CardEntry(
            card_type=card_type,
            effects=effects,
            description=description,
            learned_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        return []

    def park(self, card_type: str, description: str, reason: str) -> None:
        """Record a card that was described but cannot be expressed.

        The escape case. The card is neither forgotten nor treated as inert - it is
        marked as a known gap, which is the honest state of affairs.
        """
        self.entries[card_type] = CardEntry(
            card_type=card_type,
            effects=[],
            description=description,
            unresolved=reason,
            learned_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )

    def forget(self, card_type: str) -> bool:
        """Drop what is recorded for a card, so it is asked about afresh.

        Needed because a recorded answer is deliberately permanent - parked cards are
        never asked again, which is the point - so a *wrong* answer is permanent too, and
        nothing else can dislodge it. Sushi Go's chopsticks was parked as inexpressible
        when the vocabulary could hold it perfectly well as a flag; without this the only
        remedies were editing the file by hand or deleting the whole catalog and paying
        to redraft every card.

        Returns whether anything was actually removed.
        """
        return self.entries.pop(card_type, None) is not None

    def merge(self, other: Catalog) -> None:
        """Fold another catalog in, preferring entries that are actually playable.

        A playable entry never loses to a parked one: if a card was parked and later
        resolved, the resolution wins regardless of which side it came from.
        """
        for card_type, entry in other.entries.items():
            mine = self.entries.get(card_type)
            if mine is None or (entry.playable and not mine.playable):
                self.entries[card_type] = entry

    # -- persistence ------------------------------------------------------

    def save(self, path: Path | None = None) -> Path:
        target = Path(path or self.path or CATALOG_DIR / f"{self.game}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": SCHEMA_VERSION,
            "game": self.game,
            "cards": {k: e.to_dict() for k, e in sorted(self.entries.items())},
        }
        target.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        self.path = target
        return target


def terms_for(game_module: Any) -> dict[str, set[str]] | None:
    """The names a game module says its rules look for, keyed by vocabulary field.

    A module declares ``TERMS = {"flag": {"chopsticks"}, "count_field": {"icons"}}``.
    Read off the module rather than passed in by a caller, because what a game recognises
    is the game's to state, and a caller repeating it is one more place to get it wrong.

    ``None`` means the module declared nothing and nothing is checked - the state every
    module compiled before ``TERMS`` existed is in, and the reason the structuring agents
    are given a naming convention to fall back on rather than left to invent one.
    """
    declared = getattr(game_module, "TERMS", None)
    if declared is None:
        return None
    return {field: set(names) for field, names in declared.items()}


#: Names seen in practice for a compiled module's deck table. Not a contract: the
#: compiler is told to keep the components in a module-level table, but picks its own
#: name and shape for it every time - a dict keyed by card type, or a flat list with
#: repeats for the copy count. This is a heuristic over what has actually been observed,
#: not a guarantee.
_DECK_ATTR_NAMES = ("DECK_COMPOSITION", "DECK", "FULL_DECK", "CARDS", "CARD_TYPES")


def card_types_for(game_module: Any) -> set[str] | None:
    """The exact card-type strings a module's deck uses, if the module exposes them.

    A catalog drafted from rulebook prose alone has no way to know that "a squid
    nigiri" must be stored as the literal string ``"squid_nigiri"`` - the module's own
    deck table is the one place that string actually comes from, so reading it there is
    strictly more reliable than asking a person to submit a second document that agrees
    with a compiler's naming choice on a given run. ``None`` means nothing recognisable
    was found; the draft then runs from prose alone, same as before this existed.
    """
    for name in _DECK_ATTR_NAMES:
        value = getattr(game_module, name, None)
        if isinstance(value, dict) and value:
            return {str(key) for key in value}
        if isinstance(value, list | tuple | set | frozenset) and value:
            return {str(item) for item in value}
    return None


def load(game: str, path: Path | None = None) -> Catalog:
    """Load a game's catalog, or an empty one if it has never been written.

    A missing file is not an error - it is a game nobody has described any cards for
    yet, which is exactly the starting state the exit criterion measures from.
    """
    target = Path(path or CATALOG_DIR / f"{game}.json")
    catalog = Catalog(game=game, path=target)
    if not target.exists():
        return catalog

    data = json.loads(target.read_text(encoding="utf-8"))
    for card_type, entry in (data.get("cards") or {}).items():
        catalog.entries[card_type] = CardEntry(
            card_type=card_type,
            effects=entry.get("effects") or [],
            description=entry.get("description", ""),
            unresolved=entry.get("unresolved", ""),
            learned_at=entry.get("learned_at", ""),
        )
    return catalog


def audit(catalog: Catalog) -> list[str]:
    """Stored entries that no longer validate.

    A catalog outlives the code that wrote it. If the vocabulary changes, an entry saved
    under the old one becomes unexecutable, and it has to be reported rather than handed
    to a module that would act on it.
    """
    problems: list[str] = []
    for card_type, entry in sorted(catalog.entries.items()):
        if entry.unresolved:
            continue
        for problem in validate(entry.effects):
            problems.append(f"{card_type}: {problem}")
    return problems
