# TODO

Nothing outstanding. The previous list is done — the notes below record what
landed and where the limits are, since several items are only as good as the
archive behind them.

## Done

| Item | Where it lives |
|---|---|
| Today's menu analysed against the archive | `stats.html`, "Today" section |
| What ran on each counter | `stats.html` + `stats.json`'s `lines[]` |
| Per-dish price mean ± σ | ledger columns, hidden by default |
| Choosable ledger columns, remembered | `localStorage`, validated on read |
| Cross-language dish search | `?q=`, matches `name` and `nameDe` |
| Local star ratings, 1–5 | rate on `index.html`, review on `stats.html` |
| Policy-drift guard | `--selftest` reads both HTML pages |
| Numeric dish ids + registry | `data/dish-ids.json` |

Earlier rounds: shared facility ordering from `websites.txt`, a fully
bilingual stats page, `Choose 5` excluded from the statistics, `?lang=`
carried both ways, two `index.html` races, and `--selftest` grown from 19 to
91 assertions.

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
workflow's self-heal, and the build fails loudly if it goes missing.

**Diet precedence, the sold-out labels and the build-your-own name are
duplicated on purpose** across `tools/build_stats.py`, `index.html` and
`stats.html`, to keep the deployment dependency-free. `--selftest` asserts the
copies agree, so changing one means changing all three. A shared config
fetched at runtime was considered and rejected: it would add a failure mode on
the menu page's render path to unify three constants.

**One archive line is corrupt and stays that way.** `data/2026-08-17.jsonl` at
`07:21:25Z` records a failed API call as `"Mensa 19"` with a null `dayEntry` —
i.e. "this canteen served nothing". The generator bug is long fixed, but the
line remains: the archive is append-only history, and rewriting it would be
worse than documenting it. Anything analysing that date should expect one
snapshot with a missing German food-market menu.

## Possible next steps

Not requested, listed only so they are not rediscovered from scratch:

- Same-weekday comparison in the Today section, once there are enough weeks.
- Pruning `data/` if it ever grows enough to matter; it is append-only and
  nothing deletes old snapshots.
