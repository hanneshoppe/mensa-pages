# TODO

Next session. Notes under each item are what I found while checking, so the
work can start from a diagnosis rather than from scratch.

## 1. Carry the DE/EN choice across the index ↔ stats jump

Switching to the stats page (and back) drops the language. Both pages already
use the same `?lang=` convention, so it's the link that loses it:

```html
<!-- index.html:223 -->
<a class="stats-link" href="stats.html">Stats</a>
```

Make each page's link append its current `?lang=`. Cheapest correct fix is to
set the `href` from state when the toggle updates, rather than hardcoding it.
`stats.html` needs the same treatment for its link back.

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

---

Standing context: the machinery is finished, the archive is thin — 15 weekday
menus, 2 sell-out events, 84 dishes seen exactly once. Items 3, 6 and 7 get
substantially more useful once dishes start recurring (≈November).
