# dnup - rules

*Generated on 2026-08-29 by `google:gemini-3.6-flash` reading the rulebook page images, via
`python -m automa.scripts.build_rules_brief`. Written by a model rather than by the
author of `dnup.py` on purpose: the baseline's only knowledge of this game must not
come from the same person who wrote the module it is measured against.*

*Reviewed against both rulebook pages before use. Every change made by hand is listed
below. No rule was removed or softened; the only deletion was an unsupported inference
the model added that the rulebook contradicts.*

**Hand-added after review:**

1. **Hidden hands.** The rulebook states that each player picks up their dealt cards
   "without showing the other players". The generated brief omitted it, which would
   have left the baseline with no statement that its opponent's hand is secret. Added
   under *Card Anatomy & Hand Management*.
2. **Scope note on player count.** Added the explicit statement that this brief covers
   the two-player game only.

**Corrected after review:**

3. The generated text asserted that Play Area 1 sits on Player 1's left and Play Area 2
   on their right, and inferred that each player's Play Area 1 faces the opponent's
   Play Area 2. The rulebook says the opposite for the star player's opening turn
   ("using their Play Area 2 (to their left)"), and the seating diagram cannot settle
   it. The claim was replaced with what the rulebook actually states. This has no
   mechanical consequence - the two play areas are distinguished only by which turn
   uses which - but an assertion the source contradicts does not belong here.

**Deliberately out of scope:** the rulebook's three-to-five-player rules - letter
tokens, the two-tokens/one-token end of round, and winning at four tokens - are
superseded entirely by the two-player section and are omitted. Including them would
give the baseline a second, wrong win condition to reason about.

---

# Two-Player Game Rules

*These are the rules for the two-player game. The rulebook also covers three to five
players, which uses a different scoring system; none of that applies here.*

## Goal of the Game
The objective is to be the first player to empty your hand of all cards. The first player to win 2 rounds wins the game.

---

## Game Setup

1. **Prepare the Deck:** Remove all cards featuring the 2-dot, 3-dot, and 4-dot setup symbols from the deck. Only the remaining base cards are used for a 2-player game.
2. **Player Aid Cards:** Each player places two Player Aid cards of the same colour in front of them. These define that player's **Play Area 1** and **Play Area 2**. The two areas are distinguished only by which turn uses which; the star player's opening turn uses their Play Area 2.
3. **Deal the Cards:** Shuffle the prepared deck and deal out **all** cards equally between both players.

---

## Card Anatomy & Hand Management

* **Active vs. Inactive Values:** Each card has two numbers printed on opposite ends:
  * The **active value** is the number facing right-side up at the top of the card.
  * The **inactive value** is the upside-down number at the bottom of the card.
* **Hidden Hands:** When the cards are dealt, each player picks up their own cards **without showing them to the other player**. Your hand is secret: your opponent knows how many cards you hold, but not which cards, and you likewise cannot see theirs. Only cards on the table are visible to both players.
* **Hand Orientation Rule:** You may reorder the cards in your hand at any time, but you **must not rotate** any cards in your hand unless an action explicitly instructs you to do so. Cards must remain in the exact orientation in which you picked them up.
* **Golden Rule of Card Movement (Table to Hand):** Whenever cards move from the table into a player's hand (whether taken from an opponent or forced back due to a conflict), **every card must be rotated 180 degrees** as it enters the hand. This flips the card so its inactive value becomes its new active value, and vice versa.

---

## Sets Defined

A **set** is a collection of 1 or more cards played together that share the exact same active value.
* **Set Value:** Equal to the active value of the cards in the set.
* **Set Size:** Equal to the total number of cards in the set.

---

## Structure of Play

### Determining the Starting Player
The player holding the card with an active or inactive value of 1/5 that features the **star symbol (★)** begins the round.

### Turn Sequence & Flow
In a 2-player game, players alternate taking turn cycles. Each full turn cycle consists of **two consecutive turns**—one for each of the player's two Play Areas:
1. Turn for **Play Area 1**
2. Turn for **Play Area 2**

### Exception for the Very First Turn of a Round
The starting player (who holds the star card ★) takes **only ONE turn** on the very first turn cycle of the round, using their **Play Area 2**. After this single initial turn, play alternates normally, with each player taking two consecutive turns per cycle (Play Area 1, then Play Area 2).

---

## Steps of a Single Turn

During any individual turn (whether for Play Area 1 or Play Area 2), you must execute the following two steps in order:

### Step 1: Discard
If there is currently a set sitting in your active Play Area for this turn (played during your previous turn in that specific play area), place all cards from that set face-down into a common discard pile to the side of the play area.

### Step 2: Action
Perform **EXACTLY ONE** of the four available actions described below.

---

## Available Actions

### Action A: Play a Set of Cards
Play 1 or more cards from your hand sharing the same active value into your current active Play Area. You do not have to play all matching cards from your hand.

* **Conflict & Validation Condition:** 
  * Check the size of your set against **all** other sets currently on the table (in any play area, belonging to either player).
  * If a set of the **exact same size** (number of cards) already exists on the table:
    * Your played set **must have a strictly higher value** than that existing set.
    * If your set is higher, the player who owns the existing lower-valued set of that size must immediately return all cards from that set to their hand, **rotating each card 180 degrees** as they do so.
    * If your played set is not strictly higher in value than the existing set of the same size, playing it is **not allowed**.
  * If no set of the same size exists on the table, you may play the set regardless of its value.

### Action B: Add 1 Card to an Opponent's Set
Play **exactly 1 card** from your hand and add it to a set currently sitting in one of your opponent's play areas.

* **Conditions:**
  1. The active value of your played card **must match** the active value of the target opponent set.
  2. Adding this card increases that set's size by 1. Check if this newly enlarged set now has the same size as any other set on the table (including sets in your own play areas):
     * The value of the enlarged set **must be strictly higher** than any other existing set of the same size.
     * If it is higher, the player owning the other lower-valued set of that size must return all cards from that lower set to their hand, **rotating each card 180 degrees**.
     * If the enlarged set would not be strictly higher in value than another set of the same size, this action is **not allowed**.

### Action C: Take an Opponent's Set
Take an entire set of cards currently sitting in one of your opponent's play areas and place all of those cards into your hand.

* **Condition & Movement Rule:** This action is only allowed if the opponent currently has a set in one of their play areas. You **must rotate each card 180 degrees** as you add it to your hand.

### Action D: Rotate Your Hand
Rotate **every card** currently in your hand by 180 degrees.

* **Effect:** Swaps the active and inactive values of all cards in your hand. This action is always allowed.

---

## End of a Round and Winning the Game

* **Ending a Round:** As soon as a player plays or places the last card(s) from their hand during any turn, they **immediately win the round**. The round ends instantly.
* **Winning the Game:** The first player to win **2 rounds** wins the game.

---

## Unclear from the Rulebook

* **Deck Exhaustion:** The rulebook does not explicitly state what happens if the draw deck runs out; however, because all cards are dealt out completely at the start of each round and no drawing mechanics exist during play, deck exhaustion mid-round cannot occur.
