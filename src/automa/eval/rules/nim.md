# Nim - rules

*Hand-written. Nim is public domain and its rules fit in a paragraph, so unlike the
DNUP brief there is no source document to read and no realistic way to weaken the
baseline by writing them badly. Written to be complete and clear; if anything here is
wrong or missing, that is a bug in the evaluation, not a result.*

## The game

Nim is a two-player game of perfect information. There are several heaps of objects.
Both players can see every heap at all times. Nothing is hidden and nothing is random.

## Your turn

On your turn you must take **at least one** object, and you may take **as many as you
like**, but all from a **single heap**. You cannot take from two heaps in one turn,
and you cannot pass.

## Winning

**The player who takes the very last object wins.** Equivalently: if it is your turn
and all heaps are empty, your opponent took the last object and you have lost.

## What good play looks like

Nim is a solved game. The position is described by the XOR ("nim-sum") of the heap
sizes. If the nim-sum of all heaps is zero when it is your turn, every move loses
against perfect play. If it is non-zero, exactly the moves that make it zero win: for
some heap, reduce that heap to `heap_size XOR nim_sum`, which is only possible where
that value is smaller than the heap.

You are not required to play this way, but it is the standard of play you are being
measured against.
