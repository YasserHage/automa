"""The card catalog: what each card does, as data, learned incrementally.

Kept deliberately separate from the game modules. A module knows the *rules* - turn
order, legality, the scoring procedure - while the catalog knows what individual cards
do, and the two are learned at different times from different places. The rules come from
a rulebook once, at compile time. The cards are described by the person at the table, one
at a time, as they actually turn up.

That separation is what makes `plan.md`'s claim work: "games with dozens of cards do not
need full description at setup". A card nobody has seen yet is simply a counted deck
member with no entry here.
"""
