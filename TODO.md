# TODO

Nothing requested is outstanding. What follows is two ideas nobody asked for,
and the constraints anyone picking this up should know before changing things.
Completed work is not listed here — `git log` has it.

## Open, but not requested

- **Same-weekday comparison in the Today section.** "Is Wednesday always this
  meaty?" needs several of each weekday before it says anything; there are
  currently 16 weekday menus in total. `stats.json`'s `dishes[].dates` already
  carries what it would need.
- **Pruning `data/`.** Nothing deletes old snapshots — the archive is
  append-only by design. At ~86 KB/day it is 1.7 MB over 20 files, so roughly
  21 MB a year. Not a problem for years, and worth leaving alone until it is:
  the raw archive is the only thing everything else can be rebuilt from.

## What is thin rather than broken

The machinery is finished; the archive is not. As of 2026-08-26 it holds 16
weekday menus, **2** sell-out events, and 96 dishes of which only 7 have been
seen more than once. That is why:

- Almost every dish shows `—` for its next predicted serving. A prediction
  needs 3 sightings before a standard deviation exists.
- Every non-null price σ is exactly `0.00` — no dish has ever changed price.
- The counters section shows *what has run on each counter*, not what
  *typically* replaces what. Ranking substitution pairs by frequency needs a
  thicker archive; a single co-occurrence is not a pattern.

These fill in on their own as dishes recur — realistically from November.
None of them needs code changes to start working.

## Things worth knowing before changing anything

**`data/dish-ids.json` cannot be regenerated.** Ids are assigned in first-seen
order, not derived, so rebuilding from a finished archive allocates different
numbers — measured here, day-by-day and one-shot allocation agree on only 6 of
96. Losing it orphans every local rating. It is deliberately excluded from the
workflow's self-heal, the workflow fails before doing anything else if it is
absent, and it is validated on load because renames are recorded by hand.

**Diet precedence, the sold-out labels and the build-your-own name are
duplicated on purpose** across `tools/build_stats.py`, `index.html` and
`stats.html`, to keep the deployment dependency-free. `--selftest` reads both
pages and asserts the copies agree, so changing one means changing all three.
A shared config fetched at runtime was considered and rejected: it would add a
failure mode on the menu page's render path to unify three constants.

**Ratings are 1–5, and unrated is the absence of a key rather than a zero.** A
selectable 0 briefly existed; it was dropped because one star already says the
dish was bad. A `0` still in someone's storage stays valid and still shows on
the stats page, so do not "clean it up" by migrating those away.

**One archive line is corrupt and stays that way.** `data/2026-08-17.jsonl` at
`07:21:25Z` records a failed API call as `"Mensa 19"` with a null `dayEntry` —
i.e. "this canteen served nothing". The generator bug is long fixed, but the
line remains: the archive is append-only history, and rewriting it would be
worse than documenting it. Anything analysing that date should expect one
snapshot with a missing German food-market menu.
