# TODO

Next session. Notes under each item are what I found while checking, so the
work can start from a diagnosis rather than from scratch.

Items 1, 2, 4, 5, 8, 9 and 11 from the previous list are done (see commit
`a81a3dc`): shared facility ordering from `websites.txt`, a fully bilingual
stats page, `Choose 5` excluded from the statistics, `?lang=` carried both
ways between the pages, the two `index.html` races, and `--selftest` grown
from 19 to 36 assertions.

## 1. Analyse today's menu specifically on the stats page

Everything on the stats page is archive-wide. Add a "today" view: what is on
offer right now, its diet split versus the historical average, whether each
dish is a first-ever sighting or a repeat, and — once the archive is thicker
— how today compares to the same weekday historically.

`data/dishes.csv` already has `date`, so this is a filter plus a comparison,
not new extraction.

## 2. List which dish typically replaces which

Wanted: pairs like *Teriyaki Beef Balls* ↔ *Teriyaki Chicken Balls*.

The data already supports this and the key is the `line` column in
`data/dishes.csv` — the serving counter. The same counter runs different
dishes on different days, so a counter's dish sequence *is* the substitution
list. The requested example is literally a line named `Teriyaki` carrying both
`Teriyaki Beef Balls` and `Teriyaki Chicken Balls`.

Counters with the most variety to mine:

| facility | line | distinct dishes |
|---|---|---|
| food market | fire | 18 |
| food market | grill | 18 |
| food market | pasta della nonna | 16 |
| food market | green daily | 16 |
| food market | pasta classica | 8 |

Caveat worth designing around: with 16 weekday menus most dishes appear once,
so "typically" is not yet supported by the data. Grouping by counter works
today; ranking substitution *pairs* by frequency needs more archive. Consider
shipping the per-counter grouping now and the pairing later.

## 3. Historic price average + deviation per dish in the ledger

Add mean and standard deviation of each dish's observed price to the dish
ledger, so a dish's typical cost and how much it moves are visible per row
rather than only as a facility-wide spread.

Per-dish-day prices are already in `data/dishes.csv` (`price_students`,
`price_internal`, `price_external`), so this is an aggregation in
`tools/build_stats.py` plus columns in `stats.html` — no new extraction.

Expect it to be empty for a while, and design the empty state accordingly:

- All 96 dishes carry a price, but only **7** have been seen on more than one
  day, so 89 have no deviation to compute at all.
- Of those 7, **none** has ever changed price. Every standard deviation in the
  archive today would be exactly `0.00`.

So ship it with the same honesty as the prediction column: show mean always,
show σ only where ≥2 observations exist, and `—` otherwise. Decide whether
a column that is all-zeros-and-dashes earns its width yet, or whether
mean-only now and σ later is the better call. Archive-wide the student prices
occupy just ten distinct values (7.00–13.80), so price movement is likely to
be rare and step-like rather than continuous.

## 4. Choosable columns in the dish ledger, with a remembered choice

The ledger shows all eight columns unconditionally. Let the reader pick which
to see, defaulting to seven:

> Dish, Facility, Diet, Times seen, Last seen, Mean gap, Next predicted serving

i.e. only `First seen` is hidden by default. `dishColumns(strings)` in
`stats.html` already returns a list of `{key, label}` (it became a function
when the labels were translated), so the chooser is a filter over its result —
the table builder should render whatever subset is selected rather than the
whole array. Keep at least one column always on so the table cannot vanish.

**"Last seen" must not count today.** A dish on today's menu currently shows
today's date, which is trivially true and useless next to "next predicted
serving" — the useful answer is when it was *previously* served. Right now 12
of 96 dishes have `lastSeen == today`, so this is visible immediately.

No schema change needed: `stats.json`'s `dishes[].dates` carries the full
sighting list, so the page can take the last entry before today. Leave
`lastSeen` in the JSON as the honest maximum — this is a display rule, not a
data change. Edge case to handle: a dish whose *only* sighting is today has no
previous serving, so show `—` (or "first time today"), not today's date and
not a blank.

**Persist the choice for at least 3 months.** Plain `localStorage` under its
own key is enough — it survives indefinitely until cleared, which satisfies
"at least 3 months". Do **not** reuse the API-response cache helper: that one
carries a 5-minute TTL (`index.html:237`) and would silently discard the
preference almost immediately. Store a list of column keys, and validate on
read — unknown or removed keys must be ignored gracefully so a future change
to the column list cannot leave someone with a broken or empty table.

Note this interacts with item 3 above — per-dish price mean/deviation adds
more columns — and the chooser's own labels need to go into `STRINGS` like
everything else user-visible on the page.

## 5. Search the stats page for a specific dish

Free-text search over the dish ledger — type "teriyaki" and see just those
rows. With 96 dishes and growing, scanning is already awkward.

- Search **both** `name` and `nameDe` regardless of the active language, so
  "Rahmgulasch" finds the dish while the page is in English and vice versa.
  Match case- and accent-insensitively (`Älpler` should match "alpler").
- Compose with the existing facility filter and "only predicted" checkbox
  rather than replacing them, and preserve the query across a language toggle
  the same way the other controls now are (they live on `state`).
- Reflect it in the URL (`?q=`) so a search can be linked, consistent with
  how `?lang=` already works.
- Needs a translated placeholder and a translated "no matches" empty state —
  the latter already exists as `strings`-driven text.

Cheapest version is a substring match over the two name fields; no index, no
fuzzy matching, until the archive is big enough to justify it.

## 6. Reduce the policy duplicated across four languages

Diet precedence, the sold-out labels and the build-your-own exclusion each
still exist in some combination of `tools/build_stats.py`, `index.html`,
`stats.html` and the workflow's shell/jq.

Facility ordering and price-group ordering are no longer duplicated —
`websites.txt` position is now canonical for the first, and `stats.json`'s
`priceGroups` for the second. Both had already drifted before being unified,
which is the argument for doing the same to what remains: one generated
config the others read, without giving up the dependency-free deployment.

---

Standing context: the machinery is finished, the archive is still thin — 16
weekday menus, 2 sell-out events, and most dishes seen exactly once. Items 1,
4 and 5 get substantially more useful once dishes start recurring
(≈November).

Note on the archive: `data/2026-08-17.jsonl` at `07:21:25Z` contains a
corrupted line — a failed API call was recorded as `"Mensa 19"` with a null
`dayEntry`, i.e. "this canteen served nothing". The generator bug is fixed,
but the line stays: the archive is append-only history, and rewriting it
would be worse than documenting it. Anything analysing that date should
expect one snapshot with a missing German food-market menu.
