# Mensa Pages

A single static page that shows today's menus from ETH Zürich's Hönggerberg
canteens (food market, FUSION, …) on one screen, instead of clicking through
separate pages per facility.

Live at [mensa.h97.eu](https://mensa.h97.eu).

> **This entire site — including this README — is 100% vibecoded with
> [Claude](https://claude.com/claude-code).** No line was hand-written.

## How it works

`index.html` is a self-contained, dependency-free page (HTML/CSS/JS, no build
step). At load time it:

1. Reads `websites.txt` and pulls the facility `id` out of each URL.
2. Tries `data/<today>.jsonl` first — a snapshot pre-fetched server-side by a
   scheduled GitHub Action (see "Menu snapshots & history" below). This is a
   single fast static request instead of live-querying ETH's API, which takes
   roughly a second per call. If today's snapshot doesn't exist yet, it falls
   back to calling ETH's own public menu API directly from the browser —
   `https://idapps.ethz.ch/cookpit-pub-services/v1/` — which has CORS enabled,
   so no server-side proxy is needed. This is the same API the official
   `ethz.ch` menu pages call under the hood.
3. When it rendered from the snapshot, quietly re-checks the live API in the
   background and re-renders only if something actually changed (e.g. an
   item sold out since the snapshot was taken) — so the page stays fresh
   between the Action's 5-minute runs without a visible reload.
4. Picks out today's date client-side in **Europe/Zurich**, not the viewer's
   own timezone — otherwise a reader outside Switzerland near midnight would
   be shown the wrong day's menu. (The dates embedded in `websites.txt` are
   only there to identify which facility each URL points at; they're not used
   to decide "today".)
5. Renders each facility's dishes, facilities in `websites.txt` order (see
   "Adding more facilities"); dishes within each facility sorted vegan →
   vegetarian → omnivore (alphabetically within each), then "Choose 5",
   then sold-out items last of all, each dish tagged:
   - `O` — omnivore (default: contains meat/fish, or unclassified)
   - `V-` — vegetarian
   - `V` — vegan

   The API has no dedicated "sold out" field — it just overwrites the dish's
   `name` with the localized string "Ausverkauft"/"Sold Out" while leaving
   the description/image/price alone. Sold-out cards stay visible, grayed
   out, at the bottom of the list.

   Where the API provides prices, they're shown per customer group (e.g.
   student / internal / external), using the API's own already-localized
   group labels.

Each dish card carries a 1–5 star rating you can set, stored in this browser
only — see "Local ratings" below.

API responses are cached in `localStorage` for 5 minutes (the API itself
sends no `Cache-Control`/`ETag`), so reloading the page doesn't refetch on
every visit. Both languages are fetched concurrently on page load, so the
language toggle never has to wait on a request. The background re-check in
step 3 deliberately bypasses that cache — it exists to catch what changed, so
answering it from a cached response would defeat the point.

## How the code fits together

Four moving parts, no build step and no dependencies anywhere:

```
websites.txt ──────────────┐   which facilities exist, and in what order
                           │
.github/workflows/  ───────┤   every 5 min: fetch the API, append a snapshot
  fetch-menus.yml          │
                           ▼
                    data/<date>.jsonl        raw API responses, append-only
                           │
                           ▼
              tools/build_stats.py           the only thing that writes below
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
  data/stats.json   data/dishes.csv   data/dish-ids.json
        │                  │                  │
        ▼                  ▼                  ▼
   stats.html         (analysis)         index.html
```

### `tools/build_stats.py` — everything derived

Stdlib only, ~1,450 lines, and the single writer of the three files under
`data/` that aren't raw snapshots. Roughly in pipeline order:

- `load_dish_ids` / `assign_dish_ids` / `write_dish_ids` — the id registry.
  Validates on load, since renames are recorded by hand.
- `load_day_dish` / `build_date_rows` — walk the raw snapshots. This is where
  the API's shape is absorbed: the four-level `opening-hour-array` →
  `meal-time-array` → `line-array` → `meal` nesting, the English/German join,
  and the sold-out attribution.
- `dedup_by_dish_id` — collapses repeated snapshots of one day down to one row
  per `(date, facility, dish_id)`. **After** ids resolve, not before, or a
  renamed dish counts its day twice.
- `classify_diet`, `weekdays_between`, `price_stats` / `dish_price_stats` —
  the small rules. Gaps are weekdays because the canteens shut at weekends.
- `build_stats` / `build_dish_rows` / `write_dishes_csv` — assemble the
  outputs.
- `demo()` and the `_test_*` functions — `--selftest`, 91 assertions on
  synthetic fixtures, so it stays valid as the archive grows. Two exceptions
  read real repo files on purpose: the policy-drift guard, and the
  `websites.txt` ordering check.

### `index.html` — today's menu

Reads `data/<today>.jsonl`, falls back to the live API, then re-checks live in
the background. The parts worth knowing before editing:

- `zurichDateParts` / `nextDay` / `isoWeekday` / `zurichDateContext` — every
  date decision comes from one Europe/Zurich derivation. Don't add a second.
- `getFacilitiesData` / `refreshInBackground` — memoised per
  `` `${date}:${lang}` ``, with a monotonic `renderToken` so a slow response
  can't paint over a newer one, and rejected promises evicted so a failure
  isn't cached forever.
- `buildEnIdentityMap` / `resolveDishIdentity` / `renderRatingWidget` —
  ratings. The identity map is rebuilt per render and keyed by `line-id`
  *within one snapshot*, never globally.
- `classifyMeal` / `isSoldOut` / `mealSortKey` — the duplicated policy the
  drift guard watches.

### `stats.html` — the archive

Reads only `data/stats.json`; aggregating the daily files in the browser would
get slower every day. Five sections (`renderTodaySection`, `renderDietSection`,
`renderPriceSection`, `renderCountersSection`, `renderDishSection`), a
`STRINGS` table for both languages, and inline SVG for the price plots — no
chart library. `state` holds sort, filters, search and column choice so a
language switch can re-render everything without losing where you were.

### The workflow

Fires over the union of both DST offsets and trims to 05:00–15:00
Europe/Zurich with a `TZ=Europe/Zurich` guard, because cron is UTC-only.
Verifies the id registry exists before anything else, skips the append when
the menu is byte-identical to the last snapshot, and commits only when a file
actually changed — which is why every generated file must be a pure function
of its inputs.

## Menu snapshots & history

`.github/workflows/fetch-menus.yml` runs on a schedule (05:00–15:00
Europe/Zurich, every 5 min — GitHub's floor for scheduled workflows, and a
best-effort one, so runs can be delayed under load) and on manual dispatch.
Cron is UTC-only and cannot follow DST, so it fires over the union of both
seasons and the job trims to the real local window with a
`TZ=Europe/Zurich` check — exact across both switchovers, at the cost of a
few no-op runs that exit before making any API call.
Each run fetches every facility in both languages from the Cookpit API,
narrows each facility's weekly rota down to just today's day-entry, and
appends one JSON line to `data/<date>.jsonl` (creating the file on the day's
first run).

If the menu is byte-for-byte unchanged since the last snapshot line, the run
stops there: no line is appended, `data/stats.json` is not rebuilt, and
nothing is committed. So the scheduled runs cost one API sweep each and
produce commits only when the menu actually moves.

If any facility's fetch fails or comes back without a real name, the whole
sweep is treated as broken rather than as "this canteen served nothing
today": nothing is appended, nothing is rebuilt, nothing is committed, and
the run fails loudly instead of exiting clean. A gap in the archive for that
run is the correct outcome — a fabricated empty menu is not — and the next
scheduled tick a few minutes later retries the sweep from scratch.

All timestamps shown to a human — commit messages and the stats page header
— are Europe/Zurich wall-clock with the DST-correct abbreviation (CEST in
summer, CET in winter; MESZ/MEZ in German). `fetchedAt` inside
`data/*.jsonl` is not returned by the API — the workflow stamps it in UTC
(`date -u`) right before the sweep starts, so it marks roughly when that
run's fetch began, not per-dish. `dishes.csv`'s `sold_out_at`/
`first_seen_at`/`last_seen_at` are carried over from the `fetchedAt` of
whichever snapshot line first/last observed that state — add the Zurich
offset when reading any of these directly.

That gives two things for the price of one:

- **A fast cache** — the frontend reads the latest line of today's file
  instead of waiting on ETH's API (see "How it works" above).
- **A menu history** — since each day's file just keeps growing until
  midnight, `data/2026-08-05.jsonl` ends up as a full timeline of that day's
  runs. Every line has a `fetchedAt` timestamp and each facility's raw
  day-entry (opening hours, meal times, dishes, prices, nutrition, and each
  dish's `name`, which flips to "Ausverkauft"/"Sold Out" once it sells out).
  That's enough to answer things like "what was served on a given day" or
  "when did dish X sell out" — read the file with any JSONL-aware tool
  (`jq -c`, `pandas.read_json(path, lines=True)`, …) and compare consecutive
  lines.

Nothing prunes old files, so `data/` is a permanent, append-only archive —
delete old dates by hand if it ever grows large enough to matter.

## Statistics

`stats.html` is a second self-contained page (linked from the header) showing
what the archive adds up to: the diet mix per facility, the price spread per
customer group, and a ledger of every dish seen with a predicted next
serving.

It reads a single precomputed `data/stats.json` rather than the daily files —
aggregating ~20 growing JSONL files in the browser would get slower every
day. `tools/build_stats.py` (stdlib-only, `--selftest` for its self-check)
regenerates it from `data/*.jsonl`; the Action runs it whenever a snapshot
lands. The output is a pure function of its inputs — its `dataAsOf` is the
newest `fetchedAt` in the archive, not the time the script ran — so an
unchanged archive produces a byte-identical file and no spurious commit.

Alongside it, `data/dishes.csv` is the tidy analysis layer: one flat row per
(date, facility, dish), so `pandas.read_csv('data/dishes.csv')` replaces a
four-level traversal of the raw JSONL plus the dedup, sold-out handling and
language join that every consumer would otherwise reimplement. Columns cover
the dish (`line`, `name_en`, `name_de`, `diet`), its timing (`first_seen_at`,
`last_seen_at`, `sold_out_at`), one `price_<group>` per customer group, and
the nutrition fields.

### Dish identity: `data/dish-ids.json`

Every dish has a numeric id, and `data/dish-ids.json` maps ids to the
`[facility, name_en]` pairs that resolve to them. One id can own several
names, so a kitchen renaming a dish keeps its history instead of splitting it
into two. Renames are recorded **by hand** — appending a name to an existing
id — because inferring them from name similarity would happily merge
genuinely different dishes (`Red Thai Curry` and `Yellow Thai Curry` are not
the same dish).

**This file is the source of truth for identity and cannot be regenerated.**
Ids are assigned in the order names are first seen, not derived from content,
so rebuilding from a finished archive allocates different numbers: measured
here, a day-by-day allocation and a one-shot rebuild agree on only 6 of 96
ids. Losing it and "rebuilding" would silently renumber almost everything,
orphaning any local rating or saved link that references an id.

So unlike `stats.json` and `dishes.csv`, it is never self-healed. If it goes
missing the build fails and says to restore it from git history;
`--allow-new-registry` exists only to bootstrap a fresh one, and the workflow
checks for the file before doing anything else so a quiet day cannot stay
green while identity is broken.

Because renames are recorded by hand, the file is validated on every load:
ids must be canonical positive integers (no `01`), each entry a non-empty
`[facility, name_en]` pair, and each pair owned by exactly one id. A pair
claimed twice would otherwise let dict order decide identity and orphan the
ratings pointing at the loser, so the build refuses to run instead.

`stats.json`'s top-level `priceGroups` array lists customer groups in the
same order the CSV uses for its `price_<group>` columns: known groups first
(students, internal, external), then any others in first-seen order — one
authoritative order so the page's legend/colours and the CSV's column order
never disagree.

`sold_out_at` is what the 5-minute cadence buys. The API has no sold-out
field — it overwrites the dish's `name` with "Ausverkauft"/"Sold Out" — so
the name is gone in exactly the snapshot worth timestamping, and the event is
attributed back through `line-id` to whichever dish held that counter earlier
*the same date* (`line-id` identifies the counter, not the dish, and is
reused across days). One blind spot follows from this: a dish already showing
the placeholder in the day's first snapshot never appears under its real name
and cannot be identified at all.

`stats.json` also carries `lines[]` — one entry per serving counter with the
dishes that have run on it. A counter serving dish A on Monday and dish B on
Tuesday is what "B stood in for A" looks like in this data, so that array is
the substitution record. Note it shows *what has run on each counter*, not yet
what typically replaces what: with the archive this thin, most dishes appear
once, and a one-off co-occurrence is not a pattern. The archive also mixes
same-day variants (`Teriyaki Beef Balls` and `Teriyaki Chicken Balls` ran
together) with genuine different-day substitutions (`Red Thai Curry` →
`Yellow Thai Curry`); the `dates` arrays are what tell them apart.

Each dish additionally carries `prices` (mean/sd/min/max per customer group,
one observation per dish-day) and `dietDays`. `sd` is `null` below two
observations rather than a misleading `0.00`.

The stats page has five sections: today's offering against the archive-wide
diet split, the diet mix, the price spread, what ran on each counter, and the
dish ledger. The ledger has a column chooser (remembered in `localStorage`),
free-text search across both languages' names, and a "Last seen" that
deliberately excludes today — for a dish on today's menu the useful answer is
when it was *previously* served.

The page is fully bilingual via `?lang=`, the same convention `index.html`
uses, and the two pages carry the choice across the link between them in both
directions. Dish names come from `nameDe`, joined from the German side already
stored in every snapshot, so translation costs no extra API calls; everything
else — headings, column labels, legends, filters, caveats — is translated in
the page's own `STRINGS` table. Swiss orthography throughout: `ss`, never `ß`.

Build-your-own counters ("Choose 5") are excluded from the statistics: they
appear every single day and are not a dish. The exclusion happens **on the
page, not in the generator**, so `dishes.csv` and the raw archive keep the
complete record of what was actually offered. Each dish entry carries a
`dietDays` histogram — its own dish-days broken down by diet, always all five
keys, summing to `count` — so the page can subtract a dish from the aggregate
using the exact buckets its dish-days landed in. Subtracting the dish's total
from its *latest* classification would be wrong the moment a kitchen changed a
tag, and could produce a negative bucket.

Two things the numbers deliberately do not do:

- **No diet is inferred from a dish name.** The `vegan`/`vegetarian`/`fish`/
  `meat` split comes only from the API's `meal-class-array`, which the
  kitchens fill in by hand. ~10% of dish-days carry no tag at all; those are
  counted as `unclassified` rather than guessed. Where a dish carries several
  tags the most plant-based one wins, matching `classifyMeal()` in
  `index.html` so the two pages never disagree about the same dish.
- **No prediction below 3 sightings.** Gaps are counted in weekdays (the
  canteens are shut at weekends, so a Fri→Mon gap is one serving day, not
  three), and a next-serving estimate needs at least two gaps before a
  standard deviation exists. Almost nothing qualifies yet; the column fills
  in on its own as dishes recur.

Diet precedence, the sold-out labels and the build-your-own name (`"Choose
5"`) are deliberately duplicated between `tools/build_stats.py`, `index.html`
and `stats.html` rather than served from one generated config, so the pages
stay dependency-free (no runtime fetch on the menu page's render path just to
unify three small constants). `tools/build_stats.py --selftest` guards
against the three copies drifting apart: it reads the pages' own source and
fails if they disagree, so changing one means changing all of them.

## Local ratings

Dishes can be rated 1–5 stars on the menu page. Ratings live in this browser's
`localStorage` under `mensaDishRatings` and nowhere else: they are never
synced, never leave the machine, and disappear if site data is cleared. There
is no backend, and adding one would change what this project is. The stats
page shows them read-only — a column in the dish ledger, a "rated only"
filter, and a count/average — so there is only ever one implementation of the
control.

Two details that are easy to get wrong and are deliberately not:

- **Unrated is the absence of a key, not a zero.** Clearing a rating removes
  it rather than storing `0`, so "I haven't tried this" never reads as "this
  was terrible" and sorting by rating stays meaningful. A selectable `0`
  briefly existed alongside the clear button; it was dropped because one star
  already says the dish was bad, and a sixth control for a distinction almost
  nobody draws just read as a stray digit next to the stars. A `0` already in
  storage is still valid and still shows on the stats page — it simply cannot
  be set again.
- **Ratings key on the numeric dish id, resolved from the English name**, even
  when the page is showing German. The registry keys on English names, so a
  lookup by displayed name would silently create a second rating for the same
  dish. The menu page resolves the id by matching the displayed dish to its
  English counterpart via `line-id` *within the same snapshot* — and when it
  cannot pair the two (a failed fetch, a background refresh that disagrees
  with the snapshot), it renders no rating control rather than guessing. A
  missing control is recoverable; a rating silently attached to the wrong dish
  is not.

Entries carry a timestamp, are pruned after a year, and a timestamp
implausibly far in the future is treated as corrupt rather than kept forever.

## Constraints worth knowing before changing things

Collected here because each one costs real time to rediscover, and two of them
look like obvious cleanups until you know why they are the way they are.

**`data/dish-ids.json` cannot be regenerated** — see "Dish identity" above.
Never "rebuild" it to fix something.

**Diet precedence, the sold-out labels and the build-your-own name are
duplicated on purpose** across `tools/build_stats.py`, `index.html` and
`stats.html`, so the pages stay dependency-free. `--selftest` reads both pages
and fails if the copies disagree, so changing one means changing all three. A
shared config fetched at runtime was considered and rejected: it would put a
new failure mode on the menu page's render path to unify three constants.

**A `0` in someone's stored ratings is real data.** Ratings are 1–5 now, but a
selectable 0 briefly existed. Those entries still validate and still show on
the stats page — migrating them away would delete real ratings.

**One archive line is corrupt and stays that way.** `data/2026-08-17.jsonl` at
`07:21:25Z` records a failed API call as `"Mensa 19"` with a null `dayEntry` —
i.e. "this canteen served nothing". The generator bug that caused it is long
fixed (an incomplete sweep now aborts instead of writing a guess), but the
line remains: the archive is append-only history and rewriting it would be
worse than documenting it. Anything analysing that date should expect one
snapshot with a missing German food-market menu.

**Most statistics are empty, and that is the data rather than a bug.** A
prediction needs 3 sightings before a standard deviation exists; a price σ
needs 2. With the archive still only a few weeks deep, most dishes have been
seen once, so those columns show `—` and every non-null σ is `0.00`. They fill
in on their own as dishes recur — no code change required.

## Usage

Serve the folder over HTTP (fetching `websites.txt` needs `http://`/`https://`,
not `file://`):

```sh
python3 -m http.server 8000
```

Then open `http://localhost:8000/`.

## URL options

Both are reflected in and settable via the URL query string:

- `?lang=de` / `?lang=en` — language (default `en`)
- `?view=cards` / `?view=list` — image-grid cards or a compact list

Example: `http://localhost:8000/?lang=de&view=list`

`stats.html` honours `?lang=` too, where it swaps dish names only — its
headings and labels stay English. `?view=` does not apply there.

## Adding more facilities

Add another menu-plan URL to `websites.txt` (same `offerDay.html?id=…` format
as the existing ones) — the page picks up new facility IDs automatically.

**File order is canonical.** Both pages sort facilities by each id's position
in `websites.txt`, so reordering the lines reorders the menu page and the
stats page together. That replaced a hardcoded priority list that had already
drifted out of sync, and it keys on facility id rather than display name —
which matters, because the name differs between languages in this archive.
Because the order is load-bearing, `tools/build_stats.py` treats an unreadable
`websites.txt` as a hard error rather than quietly falling back to
alphabetical: a silent fallback would reorder `stats.json`, commit that diff,
then flip back on the next successful run.

## Deployment

Hosted on GitHub Pages, serving straight from `main` / root — pushing to
`main` redeploys automatically. The `CNAME` file pins the custom domain,
`mensa.h97.eu`, which needs a DNS `CNAME` record pointing at
`hanneshoppe.github.io`.
