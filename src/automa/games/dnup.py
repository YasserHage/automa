"""DNUP - two-player mode, implemented from the published rulebook.

DNUP is a real card game (designers Kei Kajino and Gilles-Romain Fonteny, published
by Asmodee). This module implements its *mechanics* only. Per invariant 6 no rule
text, card text or scan is reproduced here or anywhere in the repository, and the
rulebook itself stays outside version control in the ignored ``files/`` directory.

Why a real game rather than one invented to be convenient: the same rulebook becomes
the Phase 4 compiler's input, so "the compiler reproduces the hand-written module"
stops being a test against a game designed to be compilable and becomes a test
against rules somebody else wrote.

The shape of the game
---------------------
Every card carries two values, one at each end. The one that is upright is *active*;
the other is *inactive*. Any card moving from the table to a hand is rotated 180
degrees, swapping them - so a card's identity depends on its orientation, and
orientation is hidden state.

A *set* is one or more cards sharing an active value; its value is that value and its
size is the card count. Each player owns two play areas. A turn is one play area's
turn: discard whatever set is sitting in that area, then take exactly one action -
play a set into it, add a card to an opponent's set, take an opponent's set, or
rotate your entire hand. Players take their two area-turns consecutively.

Playing or enlarging a set to size *N* when another size-*N* set is on the table
requires beating it on value, and sends that one back to its owner's hand rotated.
One consequence is load-bearing for :func:`check_invariants`: **the sets on the table
always have distinct sizes**.

Emptying your hand wins the round immediately; two rounds win the match.

What is assumed rather than known
---------------------------------
:data:`DECK` is real: the rulebook does not list the cards, so the 26 two-player
value pairs were transcribed from the physical deck.

One thing is still an assumption. Deal orientation is taken to be random, which is
what happens physically when cards are picked up off the table as they lie, but the
rulebook does not say so outright. It matters more than it looks: a card's inactive
value is what it becomes when taken, so orientation at deal shifts what every hand
is worth.

There are no card effects in DNUP, so invariant 5 ("card effects are executable data
structures") has nothing to bite on here; the effect vocabulary gets its ground truth
in Phase 5 instead.
"""

from __future__ import annotations

import random
import re
from itertools import combinations

from automa.core.game import (
    COMPONENTS,
    COUNT,
    DEFAULT_PLAYERS,
    KIND_COMPONENTS,
    LABEL,
    PLY,
    SLOT,
    TO_PLAY,
    TURN,
    UNAVAILABLE,
    UNKNOWN,
    Action,
    Player,
    State,
    Viewer,
    viewer_player,
)

NAME = "dnup"
PLAYERS = DEFAULT_PLAYERS

#: The two-player deck: 26 cards, each the pair of values printed on one card,
#: transcribed from the physical deck. Index is the card id, and id 0 carries the
#: symbol that decides who opens a round.
#:
#: The distribution is deliberately skewed and the game depends on it: low values are
#: plentiful (1 and 2 appear seven times each) while high values are scarce (8 appears
#: four times, 9 three, and 10 exactly once). Since a set only displaces a same-size
#: set by *beating* it, scarcity at the top is what makes a high set worth holding -
#: and it means a value-10 set can never be larger than a single card.
DECK: tuple[tuple[int, int], ...] = (
    (1, 5),  # id 0 - opens the round
    (2, 5),
    (5, 2),
    (2, 10),
    (2, 9),
    (6, 7),
    (2, 6),
    (1, 6),
    (8, 1),
    (8, 5),
    (3, 8),
    (1, 7),
    (7, 5),
    (1, 9),
    (4, 6),
    (8, 4),
    (2, 4),
    (1, 7),
    (3, 7),
    (3, 6),
    (3, 9),
    (3, 4),
    (3, 5),
    (4, 7),
    (2, 4),
    (1, 6),
)

#: Whoever holds this card opens the round - with a single turn, using their second
#: play area. The asymmetry is in the rules and is not a quirk of this implementation.
STAR_CARD_ID = 0

#: Rounds needed to win the match.
ROUNDS_TO_WIN = 2

#: Two play areas per player.
AREAS = 2

_AREA_NAMES = ("Play Area 1", "Play Area 2")


# -- state construction ----------------------------------------------------


def _deal(seed: int, round_no: int) -> dict[Player, list[dict]]:
    """Shuffle and deal the whole deck for one round.

    Derived from ``(seed, round_no)`` rather than stored in the state, so a later
    round's deal is a pure function of information already present. Stashing future
    deals in the state would work too, and would put cards nobody has seen inside an
    object that gets rendered, logged and shown to models.
    """
    rng = random.Random(f"dnup-{seed}-{round_no}")
    cards = []
    for card_id, (a, b) in enumerate(DECK):
        up, down = (a, b) if rng.random() < 0.5 else (b, a)
        cards.append({"id": card_id, "up": up, "down": down})
    rng.shuffle(cards)
    half = len(cards) // 2
    return {PLAYERS[0]: cards[:half], PLAYERS[1]: cards[half:]}


#: Cards dealt to each player. The whole deck goes out, halved.
HAND_SIZE = len(DECK) // 2

#: The groups the table supplies at the start of a round, in the order they are asked
#: for. A slot is named ``hands.<player>`` so an answer identifies itself.
_DEAL_SLOTS: tuple[str, ...] = tuple(f"hands.{p}" for p in PLAYERS)


def _deal_question(state: State, slot: str) -> dict:
    """Ask the table for one hand.

    Both hands are asked for, Automa's included. Somebody has to read the physical
    cards, and it is the person sitting there - Automa cannot see the table. That they
    learn Automa's opening hand is a consequence of playing with real cards rather than
    a leak: the interface still shows each hand only to its owner from then on.
    """
    player = slot.split(".", 1)[1]
    whose = "Automa's" if player == PLAYERS[0] else "your"
    return {
        "kind": KIND_COMPONENTS,
        SLOT: slot,
        "round": state["round"],
        COUNT: HAND_SIZE,
        "prompt": (
            f"Round {state['round']}: read out {whose} {HAND_SIZE} cards as "
            "active/inactive pairs - the upright value first, then the one at the other "
            "end, e.g. 1/5 2/9. Order does not matter."
        ),
    }


def _start_round(state: State, round_no: int) -> State:
    """Seat a round and ask the table what was dealt.

    No longer deals: the cards are the physical ones in front of the person, and
    inventing a hand here would mean Automa playing cards nobody is holding. The seeded
    shuffle survives as the fallback in :func:`resolve_unknown` for when there is nobody
    to ask - an unattended match, and every self-play playout.
    """
    seated = {
        **state,
        "round": round_no,
        # The opening exception: the starter takes one turn, in their second area.
        # Expressing it as a starting seat rather than a special case works because
        # the seat after (player, area 2) is always (opponent, area 1).
        "area": 1,
        "hands": {p: [] for p in PLAYERS},
        "areas": {p: [[] for _ in range(AREAS)] for p in PLAYERS},
        "discard": [],
        "awaiting": list(_DEAL_SLOTS),
    }
    return _ask_for_the_deal(seated)


def _ask_for_the_deal(state: State) -> State:
    """Declare the next outstanding hand, or seat the opener once both are in.

    ``awaiting`` is tracked explicitly rather than inferred from an empty hand, because
    an empty hand is also how a round is *won* - the two states are indistinguishable
    from the cards alone.
    """
    settled = {k: v for k, v in state.items() if k != UNKNOWN}
    if state["awaiting"]:
        return {**settled, UNKNOWN: _deal_question(state, state["awaiting"][0])}

    starter = next(
        (p for p in PLAYERS if any(c["id"] == STAR_CARD_ID for c in state["hands"][p])),
        # Only reachable if the star card was mistyped or never entered. Somebody has to
        # open the round, and refusing to start would be a worse answer than a default.
        PLAYERS[0],
    )
    return {**settled, TO_PLAY: starter}


def _parse_card(token: str) -> tuple[int, int] | None:
    """``"1/5"`` -> ``(1, 5)``. Any non-digit run separates the two values."""
    match = re.fullmatch(r"(\d+)\D+(\d+)", token.strip())
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2))


def _read_hand(tokens: list[str], taken: set[int]) -> list[dict]:
    """Turn what the person typed into cards, matching each onto the printed deck.

    A token is matched to any unused card printed with those two values, in either
    orientation - which is what identifies it, since orientation is what the person is
    reporting. Duplicated pairs are interchangeable, so the first free one will do.

    A token matching nothing printed is kept anyway, under an id of its own past the end
    of the deck. Rejecting it would be the wrong trade: the count is the only validation
    a supplied deal gets, and a mistyped card is caught downstream by
    :func:`check_invariants`, where it is reported as the conservation problem it is
    rather than raised in the middle of a deal.
    """
    used = set(taken)
    cards: list[dict] = []
    for token in tokens:
        pair = _parse_card(token)
        match = None
        if pair is not None:
            match = next(
                (
                    card_id
                    for card_id, printed in enumerate(DECK)
                    if card_id not in used and sorted(printed) == sorted(pair)
                ),
                None,
            )
        if match is None:
            card = {"id": len(DECK) + len(used), "up": 0, "down": 0, "typed": token}
            if pair is not None:
                card["up"], card["down"] = pair
        else:
            card = {"id": match, "up": pair[0], "down": pair[1]}
        used.add(card["id"])
        cards.append(card)
    return cards


def _fallback_hand(state: State, player: Player, taken: set[int]) -> list[dict]:
    """The hand to play with when nobody could say what was dealt.

    With nothing supplied this is exactly the seeded deal that used to happen in
    ``_start_round``, unchanged - which is what keeps self-play, the fingerprint and the
    eval battery reproducing the same positions they always did. Once one hand *has*
    been supplied, the seeded half would collide with it, so what is left of the deck is
    dealt instead.
    """
    seeded = _deal(state["seed"], state["round"])[player]
    if not any(card["id"] in taken for card in seeded):
        return seeded

    rng = random.Random(f"dnup-{state['seed']}-{state['round']}-{player}-rest")
    rest = []
    for card_id, (a, b) in enumerate(DECK):
        if card_id in taken:
            continue
        up, down = (a, b) if rng.random() < 0.5 else (b, a)
        rest.append({"id": card_id, "up": up, "down": down})
    rng.shuffle(rest)
    return rest[:HAND_SIZE]


def resolve_unknown(state: State, answer: dict) -> State:
    """Fold a supplied hand into the state, then ask for the next one.

    Advances neither ``turn`` nor ``ply``: being told what was dealt is not a turn.
    """
    slot = answer.get(SLOT)
    awaiting = list(state.get("awaiting", []))
    if slot not in awaiting:
        # An answer to a question this state is not asking. Clearing the unknown is the
        # one thing that must happen regardless, or the match asks forever.
        return _ask_for_the_deal(state)

    player = slot.split(".", 1)[1]
    taken = {card["id"] for p in PLAYERS for card in state["hands"][p]}
    components = answer.get(COMPONENTS)
    if answer.get(UNAVAILABLE) or not components:
        hand = _fallback_hand(state, player, taken)
    else:
        hand = _read_hand(list(components), taken)

    awaiting.remove(slot)
    return _ask_for_the_deal(
        {
            **state,
            "hands": {**state["hands"], player: hand},
            "awaiting": awaiting,
        }
    )


def initial_state(seed: int | None = None) -> State:
    base: State = {
        TO_PLAY: PLAYERS[0],
        TURN: 0,
        PLY: 0,
        "seed": 0 if seed is None else seed,
        "round": 1,
        "area": 1,
        "rounds_won": {p: 0 for p in PLAYERS},
        "hands": {p: [] for p in PLAYERS},
        "areas": {p: [[] for _ in range(AREAS)] for p in PLAYERS},
        "discard": [],
        "awaiting": [],
    }
    return _start_round(base, 1)


# -- reading a state -------------------------------------------------------


def _sets_on_table(state: State) -> list[tuple[Player, int, list[dict]]]:
    return [
        (player, area, cards)
        for player in PLAYERS
        for area, cards in enumerate(state["areas"][player])
        if cards
    ]


def _by_size(state: State) -> dict[int, tuple[Player, int, list[dict]]]:
    """Sets on the table, keyed by size.

    Safe as a dict because the rules guarantee sizes are unique: any set reaching a
    size already present displaces the one that was there.
    """
    return {len(cards): (player, area, cards) for player, area, cards in _sets_on_table(state)}


def _value(cards: list[dict]) -> int:
    return cards[0]["up"]


def _other(player: Player) -> Player:
    return PLAYERS[1] if player == PLAYERS[0] else PLAYERS[0]


def _rotated(card: dict) -> dict:
    return {"id": card["id"], "up": card["down"], "down": card["up"]}


def _card_str(card: dict) -> str:
    if "typed" in card and card["up"] == 0:
        return f"?{card['typed']}?"
    return f"{card['up']}({card['down']})"


def _cards_str(cards: list[dict]) -> str:
    return " ".join(_card_str(c) for c in cards)


def _blocked_by(state: State, size: int, value: int, owner: Player, area: int) -> bool:
    """Whether a set of this size and value is illegal because of what is on the table.

    ``owner``/``area`` name the set being formed, so a set is never treated as
    conflicting with itself.
    """
    rival = _by_size(state).get(size)
    if rival is None:
        return False
    rival_owner, rival_area, rival_cards = rival
    if (rival_owner, rival_area) == (owner, area):
        return False
    return value <= _value(rival_cards)


# -- the contract ----------------------------------------------------------


def legal_actions(state: State) -> list[Action]:
    if is_terminal(state):
        return []

    player = state[TO_PLAY]
    area = state["area"]
    opponent = _other(player)
    hand = state["hands"][player]
    actions: list[Action] = []

    # A - play a set from hand into this turn's play area.
    by_value: dict[int, list[dict]] = {}
    for card in hand:
        by_value.setdefault(card["up"], []).append(card)
    for value in sorted(by_value):
        candidates = by_value[value]
        for size in range(1, len(candidates) + 1):
            if _blocked_by(state, size, value, player, area):
                continue
            seen: set[tuple[tuple[int, int], ...]] = set()
            for combo in combinations(candidates, size):
                # Two cards showing the same pair are interchangeable; listing both
                # orders would pad the tactician's menu with identical choices.
                signature = tuple(sorted((c["up"], c["down"]) for c in combo))
                if signature in seen:
                    continue
                seen.add(signature)
                actions.append(
                    {
                        LABEL: (
                            f"Play a set of {size} card(s) of value {value} "
                            f"[{_cards_str(list(combo))}] into your {_AREA_NAMES[area]}"
                        ),
                        "kind": "play",
                        "cards": [c["id"] for c in combo],
                    }
                )

    # B - add one matching card to a set the opponent has in front of them.
    for target_area, target_cards in enumerate(state["areas"][opponent]):
        if not target_cards:
            continue
        value = _value(target_cards)
        new_size = len(target_cards) + 1
        if _blocked_by(state, new_size, value, opponent, target_area):
            continue
        seen_cards: set[tuple[int, int]] = set()
        for card in hand:
            if card["up"] != value:
                continue
            signature = (card["up"], card["down"])
            if signature in seen_cards:
                continue
            seen_cards.add(signature)
            actions.append(
                {
                    LABEL: (
                        f"Add {_card_str(card)} to {opponent}'s value-{value} set in "
                        f"their {_AREA_NAMES[target_area]}, making it size {new_size}"
                    ),
                    "kind": "add",
                    "area": target_area,
                    "card": card["id"],
                }
            )

    # C - take a set the opponent has in front of them, rotating it into your hand.
    for target_area, target_cards in enumerate(state["areas"][opponent]):
        if target_cards:
            actions.append(
                {
                    LABEL: (
                        f"Take {opponent}'s set of {len(target_cards)} card(s) of value "
                        f"{_value(target_cards)} [{_cards_str(target_cards)}] from their "
                        f"{_AREA_NAMES[target_area]} into your hand (rotated)"
                    ),
                    "kind": "take",
                    "area": target_area,
                }
            )

    # D - rotate your hand. Always available, which is what guarantees this list is
    # never empty in a non-terminal state.
    actions.append(
        {
            LABEL: f"Rotate all {len(hand)} card(s) in your hand, swapping their values",
            "kind": "rotate",
        }
    )
    return actions


def apply(state: State, action: Action) -> State:
    """Resolve one action and seat the next turn. Never mutates ``state``."""
    if is_terminal(state):
        raise ValueError("the match is over; no further actions are legal")

    player = state[TO_PLAY]
    area = state["area"]
    opponent = _other(player)
    kind = action["kind"]

    hands = {p: list(state["hands"][p]) for p in PLAYERS}
    areas = {p: [list(cards) for cards in state["areas"][p]] for p in PLAYERS}
    discard = list(state["discard"])

    if kind == "rotate":
        hands[player] = [_rotated(c) for c in hands[player]]

    elif kind == "take":
        target_area = action["area"]
        taken = areas[opponent][target_area]
        if not taken:
            raise ValueError(f"{opponent} has no set in {_AREA_NAMES[target_area]} to take")
        areas[opponent][target_area] = []
        hands[player] = hands[player] + [_rotated(c) for c in taken]

    elif kind == "play":
        chosen, remaining = _split_hand(hands[player], action["cards"])
        values = {c["up"] for c in chosen}
        if len(values) != 1:
            raise ValueError(f"a set must share one active value, got {sorted(values)}")
        value = values.pop()
        if areas[player][area]:
            raise ValueError(f"your {_AREA_NAMES[area]} is not empty")
        if _blocked_by(state, len(chosen), value, player, area):
            rival = _by_size(state)[len(chosen)]
            raise ValueError(
                f"a set of size {len(chosen)} with value {_value(rival[2])} is already "
                f"on the table; value {value} does not beat it"
            )
        hands[player] = remaining
        areas[player][area] = chosen
        _evict(areas, hands, size=len(chosen), keep=(player, area))

    elif kind == "add":
        target_area = action["area"]
        target = areas[opponent][target_area]
        if not target:
            raise ValueError(f"{opponent} has no set in {_AREA_NAMES[target_area]}")
        chosen, remaining = _split_hand(hands[player], [action["card"]])
        card = chosen[0]
        value = _value(target)
        if card["up"] != value:
            raise ValueError(f"{_card_str(card)} does not match the set's value of {value}")
        new_size = len(target) + 1
        if _blocked_by(state, new_size, value, opponent, target_area):
            raise ValueError(
                f"enlarging that set to size {new_size} would not beat the size-"
                f"{new_size} set already on the table"
            )
        hands[player] = remaining
        areas[opponent][target_area] = target + [card]
        _evict(areas, hands, size=new_size, keep=(opponent, target_area))

    else:
        raise ValueError(f"unknown action kind {kind!r}")

    moved: State = {
        **state,
        TURN: state[TURN] + 1,
        PLY: state[PLY] + 1,
        "hands": hands,
        "areas": areas,
        "discard": discard,
    }

    if not hands[player]:
        return _finish_round(moved, winner_of_round=player)

    next_player, next_area = _next_seat(player, area)
    moved[TO_PLAY] = next_player
    moved["area"] = next_area
    # Step 1 of a turn: whatever is sitting in the area being played is discarded
    # before anything else happens. No decision attaches to it, so it belongs here
    # rather than in the action list.
    return _discard_area(moved, next_player, next_area)


def _next_seat(player: Player, area: int) -> tuple[Player, int]:
    """Both of a player's area-turns run back to back, then the opponent's."""
    if area + 1 < AREAS:
        return player, area + 1
    return _other(player), 0


def _split_hand(hand: list[dict], ids: list[int]) -> tuple[list[dict], list[dict]]:
    """Pull the named cards out of a hand, preserving order in both halves."""
    wanted = list(ids)
    chosen: list[dict] = []
    remaining: list[dict] = []
    for card in hand:
        if card["id"] in wanted:
            wanted.remove(card["id"])
            chosen.append(card)
        else:
            remaining.append(card)
    if wanted:
        raise ValueError(f"card id(s) {wanted} are not in that hand")
    if not chosen:
        raise ValueError("a set must contain at least one card")
    return chosen, remaining


def _evict(
    areas: dict[Player, list[list[dict]]],
    hands: dict[Player, list[dict]],
    *,
    size: int,
    keep: tuple[Player, int],
) -> None:
    """Send any other set of this size back to its owner's hand, rotated.

    This is the rule that makes table sizes unique, and it is why a hand can grow
    without its owner choosing to grow it.
    """
    for player in PLAYERS:
        for area, cards in enumerate(areas[player]):
            if (player, area) == keep or len(cards) != size or not cards:
                continue
            areas[player][area] = []
            hands[player] = hands[player] + [_rotated(c) for c in cards]


def _discard_area(state: State, player: Player, area: int) -> State:
    cards = state["areas"][player][area]
    if not cards:
        return state
    areas = {p: [list(a) for a in state["areas"][p]] for p in PLAYERS}
    areas[player][area] = []
    return {**state, "areas": areas, "discard": list(state["discard"]) + cards}


def _finish_round(state: State, winner_of_round: Player) -> State:
    rounds_won = dict(state["rounds_won"])
    rounds_won[winner_of_round] += 1
    finished = {**state, "rounds_won": rounds_won}
    if rounds_won[winner_of_round] >= ROUNDS_TO_WIN:
        return finished
    return _start_round(finished, state["round"] + 1)


def is_terminal(state: State) -> bool:
    return max(state["rounds_won"].values()) >= ROUNDS_TO_WIN


def winner(state: State) -> Player | None:
    """The first player to two rounds. DNUP cannot end level, so never ``None``."""
    if not is_terminal(state):
        raise ValueError("winner() is only defined on a terminal state")
    return next(p for p in PLAYERS if state["rounds_won"][p] >= ROUNDS_TO_WIN)


def describe_state(state: State, viewer: Viewer) -> str:
    """Render the table for one viewer.

    Each hand is shown only to its owner, Automa's included, and the seed never appears
    at all: a fallback deal is derived from it, so printing it would hand over the
    opponent's cards. The person does read Automa's opening hand off the physical table
    when they enter it - somebody has to - but that is one moment at the deal, not a
    running display of what Automa holds.
    """
    lines = [
        f"DNUP - round {state['round']}, turn {state[TURN]}. "
        f"Rounds won: {state['rounds_won'][PLAYERS[0]]}-{state['rounds_won'][PLAYERS[1]]} "
        f"(first to {ROUNDS_TO_WIN} wins).",
        f"{state[TO_PLAY]} to play, using their {_AREA_NAMES[state['area']]}.",
        "",
        "On the table (a card reads active(inactive); taking it rotates it):",
    ]
    table = _sets_on_table(state)
    if not table:
        lines.append("  nothing - both players' areas are empty")
    for owner, area, cards in table:
        lines.append(
            f"  {owner} / {_AREA_NAMES[area]}: value {_value(cards)}, size {len(cards)} "
            f"[{_cards_str(cards)}]"
        )

    lines.append("")
    if state.get("awaiting"):
        lines.append("Waiting for the cards on the table to be read out.")
    for player in PLAYERS:
        count = len(state["hands"][player])
        if viewer_player(viewer) == player:
            lines.append(f"{player}'s hand ({count}): {_cards_str(state['hands'][player])}")
        else:
            lines.append(f"{player}'s hand: {count} card(s), values hidden")
    lines.append(f"Discarded this round: {len(state['discard'])} card(s)")
    return "\n".join(lines)


def check_invariants(state: State) -> list[str]:
    problems: list[str] = []

    if state.get(UNKNOWN) or state.get("awaiting"):
        # A round waiting on its deal is sound, not broken: the cards are on the table
        # and have not been read out yet. Counting them now would report the module's
        # own opening as a conservation failure.
        return problems

    if state["area"] not in range(AREAS):
        problems.append(f"area {state['area']!r} is not one of {list(range(AREAS))}")

    # Component conservation: every card exists exactly once, somewhere, and still
    # carries the pair of values it was printed with.
    seen: list[dict] = []
    for player in PLAYERS:
        seen.extend(state["hands"][player])
        for cards in state["areas"][player]:
            seen.extend(cards)
    seen.extend(state["discard"])

    ids = sorted(c["id"] for c in seen)
    if ids != list(range(len(DECK))):
        missing = sorted(set(range(len(DECK))) - set(ids))
        extra = sorted(i for i in set(ids) if ids.count(i) > 1)
        problems.append(
            f"card conservation: {len(seen)} cards accounted for, expected {len(DECK)}"
            + (f"; missing {missing}" if missing else "")
            + (f"; duplicated {extra}" if extra else "")
        )
    for card in seen:
        if card["id"] >= len(DECK):
            # Only reachable from a deal read out at the table: something was typed that
            # is not a card in this deck. Reported rather than raised, so a mistyped
            # card shows up as the conservation problem it is and the person can see it.
            problems.append(
                f"{card.get('typed', _card_str(card))!r} was entered but is not a card "
                "in this deck"
            )
            continue
        if tuple(sorted((card["up"], card["down"]))) != tuple(sorted(DECK[card["id"]])):
            problems.append(
                f"card {card['id']} shows {_card_str(card)} but was printed "
                f"{DECK[card['id']]}"
            )

    sizes = [len(cards) for _, _, cards in _sets_on_table(state)]
    if len(sizes) != len(set(sizes)):
        problems.append(
            f"two sets on the table share a size {sorted(sizes)}; the displacement rule "
            "should make that impossible"
        )
    for owner, area, cards in _sets_on_table(state):
        if len({c["up"] for c in cards}) != 1:
            problems.append(
                f"{owner}'s set in {_AREA_NAMES[area]} mixes active values "
                f"{sorted({c['up'] for c in cards})}"
            )

    for player in PLAYERS:
        won = state["rounds_won"][player]
        if not 0 <= won <= ROUNDS_TO_WIN:
            problems.append(f"{player} has {won} round wins, outside 0..{ROUNDS_TO_WIN}")
    return problems
