# Mensa Pages

A single static page that shows today's menus from ETH Zürich's Hönggerberg
canteens (food market, FUSION, …) on one screen, instead of clicking through
separate pages per facility.

Live at [mensa.h97.eu](https://mensa.h97.eu).

> **This entire site — including this README — is 100% vibecoded with
> [Claude](https://claude.com/claude-code).** No line was hand-written.

## How it works

`index.html` is a self-contained, dependency-free page (HTML/CSS/JS, no build
step, no backend). At load time it:

1. Reads `websites.txt` and pulls the facility `id` out of each URL.
2. Calls ETH's own public menu API directly from the browser —
   `https://idapps.ethz.ch/cookpit-pub-services/v1/` — which has CORS enabled,
   so no server-side proxy is needed. This is the same API the official
   `ethz.ch` menu pages call under the hood.
3. Picks out today's date client-side (the dates embedded in `websites.txt`
   are only there to identify which facility each URL points at — they're
   not used to decide "today").
4. Renders each facility's dishes, sorted with "food market" first and
   "fusion" second, each dish tagged:
   - `O` — omnivore (default: contains meat/fish, or unclassified)
   - `V-` — vegetarian
   - `V` — vegan

API responses are cached in `localStorage` for 5 minutes (the API itself
sends no `Cache-Control`/`ETag`), so reloading the page doesn't refetch on
every visit. Both languages are fetched concurrently on page load, so the
language toggle never has to wait on a request.

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
