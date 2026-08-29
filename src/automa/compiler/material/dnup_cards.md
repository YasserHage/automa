# DNUP - the two-player card list

*Submitted rule material. The rulebook states which cards to remove for a
two-player game but never lists the values themselves - those are printed on the
cards. This file is what a user reads off their own deck and submits alongside the
rulebook, and it is the `pasted text` ingestion channel plan.md names.*

*Phase 5 replaces this with cards learned incrementally as they appear in play, so
a module built from it must keep the deck as a standalone data table rather than
baking the values into its logic.*

The two-player deck is 26 cards. Each card has two values, one printed at each end.
Whichever is upright is the card's **active** value; the other is its **inactive**
value. Rotating a card 180 degrees swaps them.

| # | values | note |
|---:|---|---|
| 0 | 1 / 5 | carries the star symbol; its holder opens the round |
| 1 | 2 / 5 |  |
| 2 | 5 / 2 |  |
| 3 | 2 / 10 |  |
| 4 | 2 / 9 |  |
| 5 | 6 / 7 |  |
| 6 | 2 / 6 |  |
| 7 | 1 / 6 |  |
| 8 | 8 / 1 |  |
| 9 | 8 / 5 |  |
| 10 | 3 / 8 |  |
| 11 | 1 / 7 |  |
| 12 | 7 / 5 |  |
| 13 | 1 / 9 |  |
| 14 | 4 / 6 |  |
| 15 | 8 / 4 |  |
| 16 | 2 / 4 |  |
| 17 | 1 / 7 |  |
| 18 | 3 / 7 |  |
| 19 | 3 / 6 |  |
| 20 | 3 / 9 |  |
| 21 | 3 / 4 |  |
| 22 | 3 / 5 |  |
| 23 | 4 / 7 |  |
| 24 | 2 / 4 |  |
| 25 | 1 / 6 |  |

How often each value appears across the 26 cards (52 printed values in total):

| value | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 |
|---|---|---|---|---|---|---|---|---|---|---|
| count | 7 | 7 | 6 | 6 | 6 | 6 | 6 | 4 | 3 | 1 |

The distribution is deliberate: low values are plentiful and high values scarce,
so a high card is worth holding. There is exactly one 10 in the deck, which means a
set of value 10 can never contain more than a single card.
