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
4. Picks out today's date client-side (the dates embedded in `websites.txt`
   are only there to identify which facility each URL points at — they're
   not used to decide "today").
5. Renders each facility's dishes, facilities sorted with "food market" first
   and "fusion" second; dishes within each facility sorted vegan → vegetarian
   → omnivore (alphabetically within each), then "Choose 5" (build-your-own),
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

API responses are cached in `localStorage` for 5 minutes (the API itself
sends no `Cache-Control`/`ETag`), so reloading the page doesn't refetch on
every visit. Both languages are fetched concurrently on page load, so the
language toggle never has to wait on a request.

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

The dish ledger carries a DE/EN toggle (`?lang=de`) that swaps dish names via
`nameDe`, joined from the German side already stored in every snapshot — so
it costs no extra API calls. The rest of the page stays English.

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

## Deployment

Hosted on GitHub Pages, serving straight from `main` / root — pushing to
`main` redeploys automatically. The `CNAME` file pins the custom domain,
`mensa.h97.eu`, which needs a DNS `CNAME` record pointing at
`hanneshoppe.github.io`.
