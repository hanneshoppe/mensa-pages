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
   between the Action's ~30-minute runs without a visible reload.
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

`.github/workflows/fetch-menus.yml` runs on a schedule (08:00–14:00
Europe/Zurich, every 30 min) and on manual dispatch. Each run fetches every
facility in both languages from the Cookpit API, narrows each facility's
weekly rota down to just today's day-entry, and appends one JSON line to
`data/<date>.jsonl` (creating the file on the day's first run), committing
and pushing the result.

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

## Adding more facilities

Add another menu-plan URL to `websites.txt` (same `offerDay.html?id=…` format
as the existing ones) — the page picks up new facility IDs automatically.

## Deployment

Hosted on GitHub Pages, serving straight from `main` / root — pushing to
`main` redeploys automatically. The `CNAME` file pins the custom domain,
`mensa.h97.eu`, which needs a DNS `CNAME` record pointing at
`hanneshoppe.github.io`.
