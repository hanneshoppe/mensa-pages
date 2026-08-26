# TODO

Next session. Notes under each item are what I found while checking, so the
work can start from a diagnosis rather than from scratch.

## 1. Carry the DE/EN choice across the index ↔ stats jump, both directions

Switching to the stats page drops the language, **and so does coming back**.
Both pages already use the same `?lang=` convention, so it is the links that
lose it — neither carries the parameter:

```html
<!-- index.html:223 -->
<a class="stats-link" href="stats.html">Stats</a>
<!-- stats.html:331 — same problem on the return trip -->
```

Make *each* page rewrite its outgoing link's `href` from current state
whenever the toggle fires, rather than hardcoding a bare filename. Picking DE
on either page must survive a round trip in either direction.

## 2. Order the restaurants identically on both pages

They genuinely disagree today:

- `index.html` sorts by `facilityPriority()` / `PRIORITY_ORDER` — food market
  first, fusion second, everything else after.
- `stats.html` renders `stats.json`'s `facilities[]`, which
  `tools/build_stats.py` sorts **alphabetically** → `Mendokoro, food market`.

Pick one order and share it. Note the sort currently lives in the generator,
so either emit the facilities pre-ordered with the same priority rule, or move
the ordering into the page and have both read one list.

## 3. Analyse today's menu specifically on the stats page

Everything on the stats page is archive-wide. Add a "today" view: what is on
offer right now, its diet split versus the historical average, whether each
dish is a first-ever sighting or a repeat, and — once the archive is thicker —
how today compares to the same weekday historically.

`data/dishes.csv` already has `date`, so this is a filter plus a comparison,
not new extraction.

## 4. Make the EN/DE toggle on the stats page visibly work

**Diagnosed: the toggle is not broken.** Driving a real click through CDP,
row 8 swaps correctly (`Alpine Herbs Grilled Pork Steak` →
`Alpenkräuter Schweinssteak vom Grill`), the URL updates to `?lang=de`, the
active button moves, and the sort/filter state survives.

The reason it *looks* dead: the ledger defaults to sorting by "times seen"
descending, and all seven of the top rows have **identical German and English
names** — `Chicken Tonkatsu Bowl`, `Choose 5`, `Cold Moiroka Ramen`,
`Miso Ramen`, `Pizza Margherita`, `Shoyu Ramen`, `Tori Ramen`. Nothing above
the fold can change, so clicking appears to do nothing. Only 52 of 91 dishes
have a German name that differs at all.

So the fix is visibility, not mechanism. Options: mark rows whose name is
translated, surface the language somewhere always-visible, or reconsider the
default sort. Item 5 removes one of the seven offenders by itself.

## 5. Exclude "Choose 5" from the stats page

It is the build-your-own counter, not a dish — it appears every single day,
so it distorts the diet mix, sits permanently at the top of the ledger, and is
one of the seven untranslatable rows from item 4.

Decide whether to drop it in the generator (cleanest, but it disappears from
`dishes.csv` too, losing a true record of what was offered) or to filter it at
render time in `stats.html` (keeps the archive complete). Recommend the
latter. Note `index.html` already special-cases it in `mealSortKey()`.

## 6. List which dish typically replaces which

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

Caveat worth designing around: with 15 weekday menus most dishes appear once,
so "typically" is not yet supported by the data. Grouping by counter works
today; ranking substitution *pairs* by frequency needs more archive. Consider
shipping the per-counter grouping now and the pairing later.

## 7. Historic price average + deviation per dish in the ledger

Add mean and standard deviation of each dish's observed price to the dish
ledger, so a dish's typical cost and how much it moves are visible per row
rather than only as a facility-wide spread.

Per-dish-day prices are already in `data/dishes.csv` (`price_students`,
`price_internal`, `price_external`), so this is an aggregation in
`tools/build_stats.py` plus columns in `stats.html` — no new extraction.

Expect it to be empty for a while, and design the empty state accordingly:

- 91 dishes carry a price, but only **7** have been seen on more than one day,
  so 84 have no deviation to compute at all.
- Of those 7, **none** has ever changed price. Every standard deviation in the
  archive today would be exactly `0.00`.

So ship it with the same honesty as the prediction column: show mean always,
show σ only where ≥2 observations exist, and `—` otherwise. Decide whether a
column that is currently all-zeros-and-dashes earns its width yet, or whether
mean-only now and σ later is the better call. Archive-wide the student prices
occupy just ten distinct values (7.00–13.80), so price movement is likely to
be rare and step-like rather than continuous.

## 12. Choosable columns in "Dish ledger & next serving", with a remembered choice

The ledger shows all eight columns unconditionally. Let the reader pick which
to see, defaulting to seven:

> Dish, Facility, Diet, Times seen, Last seen, Mean gap, Next predicted serving

i.e. only `First seen` is hidden by default. `DISH_COLUMNS` (`stats.html:714`)
is already a list of `{key, label}`, so the chooser is a filter over it — the
table builder should render whatever subset is selected rather than the whole
array. Keep at least one column always on so the table cannot vanish entirely.

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
to `DISH_COLUMNS` cannot leave someone with a broken or empty table.

Note this interacts with item 7 (per-dish price mean/deviation adds more
columns) and item 11 (the chooser's own labels need German too).

## 11. German translations for the stats page's descriptive text

Right now the DE/EN toggle swaps dish names only — every heading, column
label, legend entry and caveat note is hardcoded English. Translate the
descriptive text so DE is a real language choice rather than a name swap.

**This largely solves item 4.** The toggle looks broken today because the
seven dishes at the top of the ledger have identical German and English
names, so clicking DE changes nothing above the fold. Once the section notes,
headings and column labels translate, the effect is immediate and obvious no
matter which dishes are on screen.

Note `index.html` already has a `STRINGS` object keyed by language for
exactly this. Reuse that shape rather than inventing a second mechanism, and
keep the caveat notes' meaning intact in translation — they are the page's
honesty about thin data, not decoration.

## 8. Two `index.html` races, deferred from the Codex review

Both are real but need a network slow enough or a tab left open long enough to
hit, so they were left out of the review fix-up rather than widening that diff.

**Stale render on a fast language switch** (`index.html:596-629`, `:645-651`).
`loadAndRender` captures one language's strings, awaits the fetch, then reads
the *shared, mutable* `state.lang` afterwards. Two switches in quick
succession on a slow connection can let the older request paint last, leaving
English dishes under a DE selection. Fix with a monotonic render token:
capture language + token before awaiting, discard the result unless both still
match before touching the DOM.

**Overnight tab serves yesterday's menu** (`index.html:323-324`, `:408-417`).
`facilitiesPromises` is memoised by language only, with no date component and
no Zurich-midnight rollover, so a tab left open across midnight can reuse
yesterday's facilities when the language is toggled — even though the page has
just computed and displayed today's date. Include the Zurich date in the
memoisation key and invalidate on rollover or on `visibilitychange`.

## 9. Widen `--selftest` coverage

`--selftest` currently covers date helpers, diet precedence, price-group
ordering and one sold-out case. The review noted the gaps, and the review
findings themselves show which ones bite: duplicate snapshots, a line-id
reused across dates, mismatched localized facility names, a partial API sweep,
tiny price samples, an empty archive, and byte-level repeatability.

Worth a small fixture directory outside `data/` so these can be tested without
touching the real archive.

## 10. Policy is duplicated across four languages

Diet precedence, facility ordering, price-group ordering and the sold-out
labels each exist in some combination of `tools/build_stats.py`, `index.html`,
`stats.html` and the workflow's shell/jq. Items 2 and 5 above are both drift
that has already happened. Consider one generated config the others read,
without giving up the dependency-free deployment.

---

Standing context: the machinery is finished, the archive is thin — 15 weekday
menus, 2 sell-out events, 84 dishes seen exactly once. Items 3, 6 and 7 get
substantially more useful once dishes start recurring (≈November).

Note on the archive: `data/2026-08-17.jsonl` at `07:21:25Z` contains a
corrupted line — a failed API call was recorded as `"Mensa 19"` with a null
`dayEntry`, i.e. "this canteen served nothing". The generator bug is fixed,
but the line stays: the archive is append-only history, and rewriting it would
be worse than documenting it. Anything analysing that date should expect one
snapshot with a missing German food-market menu.
