#!/usr/bin/env python3
"""Aggregate data/*.jsonl menu snapshots into data/stats.json.

Stdlib only, no third-party deps (see README for the snapshot format).
"""
import argparse
import csv
import glob
import json
import re
import statistics
import tempfile
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

SOLD_OUT = {"sold out", "ausverkauft"}
# Precedence when a dish carries more than one meal-class tag: best-wins,
# mirroring classifyMeal() in index.html so the stats page and the menu
# page never disagree. A dish tagged both Meat and Vegan is a counter
# offering both options (e.g. "Barbecue in the Steinergarten"), not a
# contradiction to resolve toward meat.
DIET_PRECEDENCE = ["vegan", "vegetarian", "fish", "meat"]
# Display/legend order for the JSON output - independent of DIET_PRECEDENCE
# above so re-ranking classification never silently reorders the chart.
DIET_KEYS = ["vegan", "vegetarian", "fish", "meat", "unclassified"]

# Known customer groups get first claim on dishes.csv's price_* column order;
# anything else the API adds later is appended in first-seen order. Mirrors
# PRICE_GROUPS_KNOWN / computePriceGroups() in stats.html so the csv and the
# stats page never disagree about group order.
PRICE_GROUPS_KNOWN = ["students", "internal", "external"]

# websites.txt's line order is deliberately treated as the canonical facility
# order (it already lists food market/FUSION/Mendokoro in the intended
# order), so stats.json and index.html's PRIORITY_ORDER agree without
# duplicating the priority list in two languages - see TODO item 2. A future
# reader may be tempted to "fix" facilities[] back to alphabetical; don't.
WEBSITES_PATH = Path(__file__).resolve().parent.parent / "websites.txt"

# Numeric dish-id registry (TODO item 8) - the source of identity for a dish,
# not a cache: a dish's only stable handle is its (facility, name_en) pair,
# and this file is what turns that pair into a small integer that survives a
# rename. See load_dish_ids/assign_dish_ids/write_dish_ids below.
DISH_IDS_PATH = Path(__file__).resolve().parent.parent / "data" / "dish-ids.json"

# TODO item 7: diet precedence, the sold-out labels and the build-your-own
# name are duplicated in index.html and stats.html on purpose, to keep the
# deployment dependency-free (no runtime-fetched policy file on the menu
# page's render path). _test_policy_matches_html_pages() in --selftest is
# the drift guard for that duplication - see its docstring.
INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "index.html"
STATS_HTML_PATH = Path(__file__).resolve().parent.parent / "stats.html"

# meal dict key -> dishes.csv column name.
NUTRITION_FIELDS = [
    ("energy", "energy"),
    ("protein", "protein"),
    ("fat", "fat"),
    ("carbohydrates", "carbohydrates"),
    ("salt", "salt"),
    ("sugar", "sugar"),
    ("saturated-fatty-acids", "saturated_fatty_acids"),
]

DISH_IDS_README = (
    "id (string key) -> list of [facility, name_en] pairs identifying this "
    "dish. (facility, name_en) is the identity unit - two facilities serving "
    "a dish of the same name are different entries. A rename is recorded by "
    "hand: append a second [facility, name_en] pair to the existing id's "
    "list, do not create a new id for it. Never remove, reuse, or renumber "
    "an id - local ratings and saved links reference it."
)


def load_dish_ids(path=DISH_IDS_PATH, allow_new=False):
    """Load the id registry: {id (int) -> [(facility, name_en), ...]}.

    A missing file is a HARD ERROR unless allow_new is set. Unlike stats.json
    and dishes.csv, this file cannot be regenerated: ids are assigned in the
    order names are first seen, so rebuilding it from the finished archive
    allocates completely different numbers. Measured on this archive, a
    day-by-day allocation and a one-shot rebuild agree on only 6 of 96 ids.
    Silently re-minting them would orphan every local rating and every saved
    link while looking like a successful build, so restoring the file from
    git history is the only correct recovery. allow_new exists solely to
    bootstrap the registry the first time.
    """
    try:
        raw = json.loads(Path(path).read_text())
    except FileNotFoundError:
        if allow_new:
            return {}
        raise FileNotFoundError(
            f"dish id registry missing: {path}. It is the source of truth for "
            "dish identity and cannot be regenerated - restore it from git "
            "history. Pass --allow-new-registry only to bootstrap a new one."
        )
    return {
        int(k): [tuple(pair) for pair in v]
        for k, v in raw.items()
        if k != "_readme"
    }


def assign_dish_ids(registry, keys):
    """Extend registry with fresh ids for any (facility, name_en) pair in
    keys that isn't already covered by an existing id, and return
    (id_for, new_registry):
    - id_for: (facility, name_en) -> id, covering every pair in keys.
    - new_registry: registry plus the newly-assigned entries (registry
      itself is left untouched).

    Determinism is the whole point here (see TODO item 8): allocating a
    batch of new names in *sorted* (facility, name) order, rather than
    whatever order keys happens to iterate in, makes allocation a pure
    function of (registry, set of new names) - so two builds over the same
    archive assign the same ids to the same new dishes, and stats.json
    doesn't churn on every run.
    """
    id_for = {}
    for dish_id, names in registry.items():
        for pair in names:
            id_for[pair] = dish_id
    new_registry = {k: list(v) for k, v in registry.items()}
    next_id = max(registry) + 1 if registry else 1
    new_names = sorted(k for k in set(keys) if k not in id_for)
    for key in new_names:
        new_registry[next_id] = [key]
        id_for[key] = next_id
        next_id += 1
    return id_for, new_registry


def write_dish_ids(registry, path=DISH_IDS_PATH):
    """Serialise the registry with sorted numeric key order and a stable
    format, so loading an unchanged registry and re-serialising it produces
    byte-identical output - required for the workflow's
    commit-only-when-changed gating (see TODO item 8)."""
    out = {"_readme": DISH_IDS_README}
    for dish_id in sorted(registry):
        out[str(dish_id)] = [list(pair) for pair in registry[dish_id]]
    Path(path).write_text(json.dumps(out, indent=2) + "\n")


def classify_diet(meal_class_array):
    if not meal_class_array:
        return "unclassified"
    descs = {c.get("desc", "").strip().lower() for c in meal_class_array if c}
    for tier in DIET_PRECEDENCE:
        if tier in descs:
            return tier
    return "unclassified"


def weekdays_between(d1, d2):
    """Business-day (Mon-Fri) distance from d1 to d2, d2 > d1. A canteen
    closed Fri-Sun means a Fri->Mon sighting gap is 1 serving-day, not 3."""
    n = 0
    for i in range(1, (d2 - d1).days + 1):
        if (d1 + timedelta(days=i)).weekday() < 5:
            n += 1
    return n


def add_weekdays(d, n):
    """Advance date d by n (>=1) weekdays, landing on a weekday."""
    n = max(n, 1)
    while n > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def price_stats(prices):
    if not prices:
        return None
    prices = sorted(prices)
    if len(prices) == 1:
        p25 = p50 = p75 = prices[0]
    else:
        # method="inclusive": the default "exclusive" method extrapolates
        # beyond the observed range on small samples (e.g. two points),
        # which would render a box-plot whisker shorter than its own box.
        p25, p50, p75 = statistics.quantiles(prices, n=4, method="inclusive")
    return {
        "n": len(prices),
        "min": round(prices[0], 2),
        "p25": round(p25, 2),
        "median": round(p50, 2),
        "p75": round(p75, 2),
        "max": round(prices[-1], 2),
        "mean": round(statistics.mean(prices), 2),
    }


def dish_price_stats(prices):
    """Per-dish price summary for stats.json's dishes[].prices (TODO item
    3) - n/mean/min/max reuse price_stats()'s already-rounded numbers rather
    than a second rounding path; the quartiles that field carries aren't
    needed at dish granularity. sd is the sample standard deviation, and
    stays honestly null (not 0.00) with a single observation - there's no
    deviation to measure from one point, the same honesty rule the
    prediction column already follows."""
    stats = price_stats(prices)
    if stats is None:
        return None
    return {
        "n": stats["n"],
        "mean": stats["mean"],
        "sd": round(statistics.stdev(prices), 2) if len(prices) >= 2 else None,
        "min": stats["min"],
        "max": stats["max"],
    }


def iter_lines(facilities):
    """Yield (facility_name, facility_id, line_name, meal) for every
    line-array entry with a meal - real dish or sold-out placeholder alike.
    Shared traversal for both languages; iter_meals below layers the
    real-dish-only filtering stats.json wants on top of it."""
    for fac in facilities or []:
        day = fac.get("dayEntry")
        if day is None:
            continue
        for hour in day.get("opening-hour-array") or []:
            for meal_time in hour.get("meal-time-array") or []:
                for line in meal_time.get("line-array") or []:
                    meal = line.get("meal")
                    if meal is not None:
                        yield fac["name"], fac.get("id"), line.get("name"), meal


def iter_meals(facilities_en):
    """Yield (facility_name, facility_id, line_name, dish_name, meal) for
    every real dish of the day."""
    for facility, fid, line, meal in iter_lines(facilities_en):
        name = meal.get("name")
        if name is None or name.strip().lower() in SOLD_OUT:
            continue
        yield facility, fid, line, name, meal


def load_day_dish(data_dir):
    """Read every data/*.jsonl file and dedup down to one meal per
    (date, facility, dish name), keeping the most recent snapshot line seen
    for that day (multiple snapshot lines per day repeat the same dishes).

    Also returns facility_id_for: facility_name -> id (first seen), so
    build_stats can order facilities[] by websites.txt position without a
    second pass over the archive; and day_dish_line: (date, facility, dish)
    -> serving-counter name, last-write-wins like day_dish itself, feeding
    the lines[] substitution grouping (TODO item 2)."""
    day_dish = {}
    day_dish_line = {}
    facility_id_for = {}
    latest_fetched_at = None  # max fetchedAt seen, for a deterministic "as of"
    for path in sorted(glob.glob(str(data_dir / "*.jsonl"))):
        d = Path(path).stem
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        with open(path) as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                snapshot = json.loads(raw_line)
                fetched_at = snapshot.get("fetchedAt")
                if fetched_at and (latest_fetched_at is None or fetched_at > latest_fetched_at):
                    latest_fetched_at = fetched_at
                for facility, fid, line_name, dish, meal in iter_meals(snapshot["facilities"]["en"]):
                    day_dish[(d, facility, dish)] = meal
                    day_dish_line[(d, facility, dish)] = line_name
                    facility_id_for.setdefault(facility, fid)
    return day_dish, latest_fetched_at, facility_id_for, day_dish_line


def ordered_groups(seen_order, known):
    """known-first-then-first-seen order, mirroring computePriceGroups() in
    stats.html so dishes.csv's price_* columns and the stats page never
    disagree about group order."""
    present_known = [g for g in known if g in seen_order]
    unknown = [g for g in seen_order if g not in known]
    return present_known + unknown


def parse_websites_order(path):
    """Extract id= values out of websites.txt, first-occurrence order - the
    canonical facility order (see WEBSITES_PATH). websites.txt is a
    committed, required input sitting next to this script; if it can't be
    read, something is badly wrong, so this raises rather than degrading to
    alphabetical order. A silent fallback here would make a transient CI
    read failure flip facilities[] order, which commits (a real diff), and
    then flips back and commits again on the next successful run - exactly
    the spurious-commit churn the commit-only-when-changed design exists to
    prevent."""
    try:
        text = Path(path).read_text()
    except OSError as e:
        raise OSError(
            f"cannot read required facility-order file {path}: {e} - "
            "websites.txt must be a committed, readable file (see WEBSITES_PATH)"
        ) from e
    ids = []
    for line in text.splitlines():
        m = re.search(r"[?&]id=(\w+)", line)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def facility_sort_key(name, facility_id, order):
    """Sort key placing facility_id at its websites.txt position; unknown or
    unmatched ids sort after all known ones, then alphabetically by name -
    so the order stays total and deterministic even as new facilities show
    up in the API before websites.txt is updated."""
    try:
        idx = order.index(facility_id)
    except ValueError:
        idx = len(order)
    return (idx, name)


def build_date_rows(d, snapshots):
    """Turn one date's snapshots (chronologically sorted by fetchedAt) into
    tidy rows, one per (facility, dish) real dish seen that day, for
    dishes.csv. Split out from build_dish_rows so --selftest can exercise
    the sold-out join below on a couple of synthetic snapshots without
    touching data/*.jsonl.

    The API has no sold-out flag - it overwrites the meal's name with
    "Sold Out"/"Ausverkauft" once a dish runs out, so it can't be joined by
    name anymore. line-id survives the overwrite and identifies the serving
    line (not the dish - it's reused across days for different dishes), so
    the join below is (facility_id, line-id) -> last real dish name seen on
    that line, reset fresh for every date. Keyed by facility_id rather than
    the display name used elsewhere in this function, because the EN/DE
    sides can carry different names for the same facility (e.g. one side's
    fetch failing) - an id match is exact where a name match would silently
    miss and fall back to English.

    Returns (rows, de_name_for, price_groups_seen):
    - rows: (facility, dish) -> row dict for this date, name_de already
      falling back to the English name where no German line-id match exists.
    - de_name_for: (facility, dish) -> matched German name, this date only
      (absent, not None, where no match exists) - for build_dish_rows to
      track the dish's most-recently-seen German name across dates.
    - price_groups_seen: customer-group-desc values, first-seen order.
    """
    first_seen, last_seen = {}, {}
    meal_for, line_for, de_name_for = {}, {}, {}
    facility_id_for = {}
    line_last_dish = {}  # (facility_id, line-id) -> current real dish name, this date only
    sold_out_at = {}
    price_groups_seen = []

    for snapshot in snapshots:
        fetched_at = snapshot.get("fetchedAt")

        de_line_name = {}  # (facility_id, line-id) -> German name, this snapshot only
        for _facility_de, fid_de, _line, meal in iter_lines(snapshot["facilities"].get("de")):
            lid, name = meal.get("line-id"), meal.get("name")
            if fid_de is not None and lid is not None and name is not None:
                de_line_name[(fid_de, lid)] = name

        for facility, fid, line_name, meal in iter_lines(snapshot["facilities"]["en"]):
            facility_id_for.setdefault(facility, fid)
            lid, name = meal.get("line-id"), meal.get("name")
            if name is None:
                continue
            if name.strip().lower() in SOLD_OUT:
                # Not a dish of its own (matches iter_meals) - attribute the
                # timestamp to whichever dish this line-id served earlier
                # today, if any.
                dish = line_last_dish.get((fid, lid)) if fid is not None and lid is not None else None
                if dish is not None:
                    sold_out_at.setdefault((facility, dish), fetched_at)
                continue

            key = (facility, name)
            first_seen.setdefault(key, fetched_at)
            last_seen[key] = fetched_at
            meal_for[key] = meal
            line_for[key] = line_name
            # de_name_for tracks the *current* (as of this, the latest,
            # sighting) German match, mirroring meal_for/line_for's "last
            # write wins" - so a line-id mismatch on the day's final
            # snapshot correctly falls back to English even if an earlier
            # snapshot that day happened to match.
            de_name = de_line_name.get((fid, lid)) if fid is not None and lid is not None else None
            if de_name is not None and de_name.strip().lower() not in SOLD_OUT:
                de_name_for[key] = de_name
            else:
                de_name_for.pop(key, None)
            if fid is not None and lid is not None:
                line_last_dish[(fid, lid)] = name

            for p in meal.get("meal-price-array") or []:
                group = p.get("customer-group-desc")
                if group is not None and group not in price_groups_seen:
                    price_groups_seen.append(group)

    rows = {}
    for key, meal in meal_for.items():
        facility, dish = key
        prices = {p.get("customer-group-desc"): p.get("price")
                  for p in meal.get("meal-price-array") or []
                  if p.get("customer-group-desc") is not None and p.get("price") is not None}
        rows[key] = {
            "date": d,
            "facility": facility,
            "facility_id": facility_id_for.get(facility) or "",
            "line": line_for[key],
            "name_en": dish,
            "name_de": de_name_for.get(key, dish),  # fallback to English
            "diet": classify_diet(meal.get("meal-class-array")),
            "first_seen_at": first_seen[key],
            "last_seen_at": last_seen[key],
            "sold_out_at": sold_out_at.get(key, ""),
            "prices": prices,
            "nutrition": {col: (meal.get(src) or "") for src, col in NUTRITION_FIELDS},
        }
    return rows, de_name_for, price_groups_seen


def build_dish_rows(data_dir):
    """Read every data/*.jsonl file into dishes.csv's tidy rows.

    Returns (rows, latest_de_name, price_groups):
    - rows: (date, facility, name_en) -> row dict, ready for write_dishes_csv.
    - latest_de_name: (facility, dish) -> German name from the dish's most
      recent sighting (None if that date had no line-id match) - the same
      "last write wins across dates" rule build_stats already uses for
      diet/nutrition, so stats.json's nameDe never disagrees with
      dishes.csv about which sighting is "latest".
    - price_groups: customer-group-desc values, first-seen order across the
      whole archive.
    """
    rows = {}
    latest_de_name = {}
    price_groups_seen = []

    for path in sorted(glob.glob(str(data_dir / "*.jsonl"))):
        d = Path(path).stem
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        with open(path) as f:
            snapshots = [json.loads(line) for line in f if line.strip()]
        snapshots.sort(key=lambda s: s.get("fetchedAt") or "")

        date_rows, date_de_name, date_groups = build_date_rows(d, snapshots)
        for key, row in date_rows.items():
            rows[(d, row["facility"], row["name_en"])] = row
            # Overwritten every date this dish appears, in ascending date
            # order, so it ends up None if the *latest* date had no match -
            # even if an earlier date happened to match.
            latest_de_name[key] = date_de_name.get(key)
        for g in date_groups:
            if g not in price_groups_seen:
                price_groups_seen.append(g)

    return rows, latest_de_name, price_groups_seen


def write_dishes_csv(rows, price_groups, id_for, out_path):
    # dish_id first (TODO item 8) - the stable handle a reader should join
    # on, ahead of the human-readable columns that can be renamed out from
    # under it.
    fixed = ["dish_id", "date", "facility", "facility_id", "line", "name_en", "name_de", "diet",
             "first_seen_at", "last_seen_at", "sold_out_at"]
    nutrition_cols = [col for _src, col in NUTRITION_FIELDS]
    fieldnames = fixed + [f"price_{g}" for g in price_groups] + nutrition_cols

    # lineterminator="\n": csv defaults to CRLF, but git stores LF, so a
    # CRLF file would differ from its own checkout on every CI run and
    # commit ~110 times a day instead of only when the menu changes.
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for key in sorted(rows):  # (date, facility, name_en) - deterministic output
            row = rows[key]
            out = {k: row[k] for k in fixed if k != "dish_id"}
            # Direct lookup, not .get(): id_for must cover every (facility,
            # name_en) pair rows can produce (both are derived from the same
            # archive traversal) - a miss means that invariant broke and
            # should fail loudly, not silently emit a blank id column.
            out["dish_id"] = id_for[(row["facility"], row["name_en"])]
            for g in price_groups:
                out[f"price_{g}"] = row["prices"].get(g, "")
            out.update(row["nutrition"])
            writer.writerow(out)


def build_stats(data_dir, latest_de_name=None, price_groups=None, websites_path=WEBSITES_PATH, id_for=None):
    day_dish, data_as_of, facility_id_for, day_dish_line = load_day_dish(data_dir)

    # id_for: (facility, dish) -> numeric dish id (TODO item 8). Callers that
    # care about stable ids across builds (the real __main__ run, and the
    # rename test below) pass a registry-backed mapping in; a caller that
    # doesn't (most of the self-tests) gets one assigned fresh, in-memory,
    # from an empty registry - deterministic and self-contained, but not
    # persisted, so it says nothing about what a real build would assign.
    if id_for is None:
        id_for, _ = assign_dish_ids({}, {(facility, dish) for _d, facility, dish in day_dish})

    facility_days = defaultdict(set)
    facility_dishdays = Counter()
    facility_diet = defaultdict(Counter)
    facility_prices = defaultdict(lambda: defaultdict(list))

    overall_days = set()
    overall_diet = Counter()
    overall_prices = defaultdict(list)

    # Keyed by dish id, not (facility, dish) name, from here on: two names
    # that the registry maps to the same id (a recorded rename) must
    # accumulate into one dish history, not two - see by_dish_name/
    # by_dish_facility below, which track which (facility, name) pair
    # produced the *latest* sighting for display purposes.
    by_dish_dates = defaultdict(list)  # dish id -> dates, ascending
    by_dish_latest_meal = {}  # dish id -> most recent meal seen
    by_dish_name = {}  # dish id -> name_en of the most recent sighting
    by_dish_facility = {}  # dish id -> facility of the most recent sighting
    by_dish_diet = defaultdict(Counter)  # dish id -> Counter of each dish-day's own diet
    by_dish_prices = defaultdict(lambda: defaultdict(list))  # dish id -> group -> prices, one per dish-day
    # lines[] is intentionally NOT merged by id - it groups by the literal
    # dish name a serving counter ran, and still shows each name it saw as
    # its own entry (carrying the shared id, see below), rather than folding
    # a rename together at the counter level too.
    by_line_dish_dates = defaultdict(list)  # (facility, line, dish) -> dates, ascending

    for (d, facility, dish), meal in sorted(day_dish.items()):
        diet = classify_diet(meal.get("meal-class-array"))
        line = day_dish_line[(d, facility, dish)]
        dish_id = id_for[(facility, dish)]

        facility_days[facility].add(d)
        facility_dishdays[facility] += 1
        facility_diet[facility][diet] += 1
        overall_days.add(d)
        overall_diet[diet] += 1
        by_dish_diet[dish_id][diet] += 1

        for p in meal.get("meal-price-array") or []:
            group, price = p.get("customer-group-desc"), p.get("price")
            if group is None or price is None:
                continue
            facility_prices[facility][group].append(price)
            overall_prices[group].append(price)
            by_dish_prices[dish_id][group].append(price)

        by_dish_dates[dish_id].append(d)  # sorted-by-date loop -> stays ascending even across a merge
        by_dish_latest_meal[dish_id] = meal  # last write = most recent (sorted by date)
        by_dish_name[dish_id] = dish
        by_dish_facility[dish_id] = facility
        by_line_dish_dates[(facility, line, dish)].append(d)

    def facility_entry(name, days_set, dishdays, diet_counter, price_lists):
        return {
            "name": name,
            "days": len(days_set),
            "dishDays": dishdays,
            # None (not a crash) when days_set is empty - see the empty-
            # archive self-test.
            "firstSeen": min(days_set) if days_set else None,
            "lastSeen": max(days_set) if days_set else None,
            "diet": {k: diet_counter.get(k, 0) for k in DIET_KEYS},
            "prices": {g: price_stats(v) for g, v in sorted(price_lists.items()) if v},
        }

    # facility_days only gains a key inside the loop above, i.e. only for a
    # facility that actually served >=1 deduped dish. A facility with a
    # non-null dayEntry but an empty line-array (e.g. FUSION, closed for
    # summer) never touches it, so it's omitted here automatically and will
    # reappear on its own once it starts serving again - no dayEntry check.
    #
    # Ordered by websites.txt's line position, not alphabetically - that
    # file already lists facilities in the intended order (food market,
    # FUSION, Mendokoro) and is the single source both this script and
    # index.html read, so the two pages can't drift apart again (TODO
    # item 2). Do not "fix" this back to sorted(facility_days).
    order = parse_websites_order(websites_path)
    facility_names = sorted(facility_days,
                             key=lambda n: facility_sort_key(n, facility_id_for.get(n), order))
    facilities = [
        facility_entry(name, facility_days[name], facility_dishdays[name],
                        facility_diet[name], facility_prices[name])
        for name in facility_names
    ]
    overall = facility_entry("all", overall_days, sum(facility_dishdays.values()),
                              overall_diet, overall_prices)

    dishes = []
    for dish_id, dates_seen in by_dish_dates.items():
        facility = by_dish_facility[dish_id]
        dish = by_dish_name[dish_id]
        date_objs = [date.fromisoformat(d) for d in dates_seen]
        gaps = [weekdays_between(date_objs[i], date_objs[i + 1]) for i in range(len(date_objs) - 1)]
        mean_gap = round(float(statistics.mean(gaps)), 2) if gaps else None
        sd_gap = round(statistics.stdev(gaps), 2) if len(gaps) >= 2 else None

        prediction = None
        if len(dates_seen) >= 3:
            last = date_objs[-1]
            next_date = add_weekdays(last, round(mean_gap))
            earliest = add_weekdays(last, round(mean_gap - sd_gap))
            latest = add_weekdays(last, round(mean_gap + sd_gap))
            prediction = {
                "nextDate": next_date.isoformat(),
                "sigmaWeekdays": sd_gap,
                "earliest": earliest.isoformat(),
                "latest": latest.isoformat(),
            }

        meal = by_dish_latest_meal[dish_id]
        # Per-customer-group price mean/sd (TODO item 3) - omitted entirely
        # (not an empty dict) for a dish that never carried a price, and
        # per-group for a dish that only sometimes did, mirroring
        # facility_entry's "prices" above.
        dish_prices = {g: dish_price_stats(v)
                        for g, v in sorted(by_dish_prices.get(dish_id, {}).items())}
        entry = {
            "id": dish_id,
            "name": dish,
            "nameDe": (latest_de_name or {}).get((facility, dish)) or dish,
            "facility": facility,
            "diet": classify_diet(meal.get("meal-class-array")),
            # Per-dish-day diet histogram, using each sighting's own
            # classification (mirrors facility_diet above, tallied in the
            # same loop so the two can't diverge) - not just this dish's
            # latest "diet" above. Lets a consumer (e.g. stats.html
            # excluding the Choose-5 build-your-own dish from the diet mix)
            # subtract a dish's history from the exact bucket(s) it landed
            # in, instead of assuming it's all under the latest diet.
            "dietDays": {k: by_dish_diet[dish_id].get(k, 0) for k in DIET_KEYS},
            "count": len(dates_seen),
            "firstSeen": dates_seen[0],
            "lastSeen": dates_seen[-1],
            "dates": dates_seen,
            "gapsWeekdays": gaps,
            "meanGapWeekdays": mean_gap,
            "sdGapWeekdays": sd_gap,
            "prediction": prediction,
        }
        if dish_prices:
            entry["prices"] = dish_prices
        dishes.append(entry)
    dishes.sort(key=lambda x: (-x["count"], x["name"]))

    # lines[]: which dish typically replaces which (TODO item 2), grouped by
    # serving counter (facility, line) - the same counter runs different
    # dishes on different days, so its dish sequence is the substitution
    # list. Current limitation: with 16 weekday menus most dishes appear
    # once, so this shows what has run on a counter, not yet a *ranking* of
    # substitution pairs by frequency - that needs a thicker archive.
    by_line_dishes = defaultdict(list)  # (facility, line) -> [{dish summary}]
    for (facility, line, dish), dates_seen in by_line_dish_dates.items():
        by_line_dishes[(facility, line)].append({
            "id": id_for[(facility, dish)],
            "name": dish,
            "nameDe": (latest_de_name or {}).get((facility, dish)) or dish,
            "count": len(dates_seen),
            "firstSeen": dates_seen[0],
            "lastSeen": dates_seen[-1],
            "dates": dates_seen,
        })
    lines = []
    for facility, line in sorted(by_line_dishes,
                                  key=lambda fl: (facility_sort_key(fl[0], facility_id_for.get(fl[0]), order), fl[1])):
        dish_list = sorted(by_line_dishes[(facility, line)], key=lambda x: (-x["count"], x["name"]))
        lines.append({
            "facility": facility,
            "line": line,
            "dishDays": sum(x["count"] for x in dish_list),
            "dishes": dish_list,
        })

    return {
        "dataAsOf": data_as_of,
        # Authoritative group order (known groups first, then first-seen) so
        # this and dishes.csv's price_<group> columns never disagree - see
        # ordered_groups().
        "priceGroups": ordered_groups(price_groups or [], PRICE_GROUPS_KNOWN),
        "dateRange": {
            "from": min(overall_days) if overall_days else None,
            "to": max(overall_days) if overall_days else None,
            "days": len(overall_days),
        },
        "facilities": facilities,
        "overall": overall,
        "dishes": dishes,
        "lines": lines,
    }


def demo():
    """assert-based self check: weekday-gap helper + diet precedence."""
    mon, tue, fri = date(2026, 8, 3), date(2026, 8, 4), date(2026, 8, 7)
    next_mon = date(2026, 8, 10)
    assert weekdays_between(mon, tue) == 1
    assert weekdays_between(fri, next_mon) == 1  # weekend doesn't count
    assert weekdays_between(mon, next_mon) == 5
    assert add_weekdays(fri, 1) == next_mon
    assert add_weekdays(mon, 4) == fri

    assert classify_diet(None) == "unclassified"
    assert classify_diet([]) == "unclassified"
    assert classify_diet([{"desc": "Meat"}]) == "meat"
    # Real case in the archive: a counter offering both a meat and a vegan
    # option is tagged with both - best-wins, matching classifyMeal() in
    # index.html.
    assert classify_diet([{"desc": "Meat"}, {"desc": "Vegan"}]) == "vegan"
    assert classify_diet([{"desc": "Fish"}, {"desc": "Vegetarian"}]) == "vegetarian"
    assert classify_diet([{"desc": "Unknown"}]) == "unclassified"

    assert ordered_groups(["external", "students", "unknown", "internal"],
                           PRICE_GROUPS_KNOWN) == ["students", "internal", "external", "unknown"]

    # method="inclusive" (see price_stats): the default "exclusive" method
    # extrapolates quartiles outside the observed range on small samples -
    # e.g. [7, 8] gives p25=6.75/p75=8.25, both outside min/max.
    ps = price_stats([7, 8])
    assert ps["p25"] >= ps["min"]
    assert ps["p75"] <= ps["max"]

    # dishes.csv sold-out attribution: the API has no sold-out flag, it just
    # overwrites the meal's name - so a dish that sells out partway through
    # the day must still be found and dated via its line-id, not its name.
    def _line(line_id, name, **extra):
        return {"name": "Line A", "meal": {"line-id": line_id, "name": name, **extra}}

    def _facility(meal_entry):
        return [{"id": "1", "name": "Test Facility", "dayEntry": {
            "opening-hour-array": [{"meal-time-array": [{"line-array": [meal_entry]}]}]}}]

    snapshots = [
        {"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {
            "en": _facility(_line(999, "Test Dish", **{"meal-class-array": [{"desc": "Meat"}]})),
            "de": _facility(_line(999, "Testgericht")),
        }},
        {"fetchedAt": "2026-08-01T09:00:00Z", "facilities": {
            "en": _facility(_line(999, "Sold Out")),
            "de": _facility(_line(999, "Ausverkauft")),
        }},
    ]
    rows, de_name_for, _groups = build_date_rows("2026-08-01", snapshots)
    row = rows[("Test Facility", "Test Dish")]
    assert row["first_seen_at"] == "2026-08-01T06:00:00Z"
    assert row["last_seen_at"] == "2026-08-01T06:00:00Z"
    assert row["sold_out_at"] == "2026-08-01T09:00:00Z"  # joined via line-id, not name
    assert row["name_de"] == "Testgericht"
    assert de_name_for[("Test Facility", "Test Dish")] == "Testgericht"

    _test_duplicate_snapshots_dedup()
    _test_line_id_reuse_across_dates()
    _test_mismatched_facility_names()
    _test_sold_out_in_first_snapshot()
    _test_empty_archive()
    _test_byte_repeatability()
    _test_facility_order_from_websites_txt()
    _test_dish_diet_days_matches_history()
    _test_dish_prices_and_lines()
    _test_dish_id_new_name_gets_next_free()
    _test_dish_id_stable_across_rebuild()
    _test_dish_id_rename_merges_sightings()
    _test_dish_id_allocation_deterministic_for_batch()
    _test_dish_id_registry_reserialize_byte_identical()
    _test_policy_matches_html_pages()

    print("demo: all assertions passed")


# --- Fixture helpers for the tests below: build the smallest possible
# facilities-array shape the API produces, without touching data/*.jsonl. ---

def _mk_meal(line_id, name, **extra):
    return {"name": "Line A", "meal": {"line-id": line_id, "name": name, **extra}}


def _mk_facility(fid, name, line_entries):
    return [{"id": fid, "name": name, "dayEntry": {
        "opening-hour-array": [{"meal-time-array": [{"line-array": line_entries}]}]}}]


def _write_jsonl(path, snapshots):
    path.write_text("\n".join(json.dumps(s) for s in snapshots) + "\n")


def _test_duplicate_snapshots_dedup():
    """The same dish repeated across several snapshot lines in one day must
    dedup to a single dish-day, not be counted once per snapshot."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        en = _mk_facility("19", "food market", [_mk_meal("1", "Ramen")])
        snapshots = [{"fetchedAt": f"2026-08-01T0{h}:00:00Z", "facilities": {"en": en}}
                     for h in (6, 7, 8)]
        _write_jsonl(d / "2026-08-01.jsonl", snapshots)

        day_dish, _as_of, _fid_for, _line_for = load_day_dish(d)
        assert len(day_dish) == 1
        assert ("2026-08-01", "food market", "Ramen") in day_dish


def _test_line_id_reuse_across_dates():
    """A line-id is reused across dates for unrelated dishes (it identifies
    the serving counter, not the dish). A sellout attributed via line-id on
    one date must not leak onto a different dish that reuses the same
    line-id on another date - the highest-value gap, since it's the bug
    class that would silently corrupt data."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        day1 = [
            {"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {
                "en": _mk_facility("19", "food market", [_mk_meal("7", "Dish A")])}},
            {"fetchedAt": "2026-08-01T09:00:00Z", "facilities": {
                "en": _mk_facility("19", "food market", [_mk_meal("7", "Sold Out")])}},
        ]
        day2 = [
            {"fetchedAt": "2026-08-02T06:00:00Z", "facilities": {
                "en": _mk_facility("19", "food market", [_mk_meal("7", "Dish B")])}},
        ]
        _write_jsonl(d / "2026-08-01.jsonl", day1)
        _write_jsonl(d / "2026-08-02.jsonl", day2)

        rows, _latest_de_name, _groups = build_dish_rows(d)
        row_a = rows[("2026-08-01", "food market", "Dish A")]
        row_b = rows[("2026-08-02", "food market", "Dish B")]
        assert row_a["sold_out_at"] == "2026-08-01T09:00:00Z"
        assert row_b["sold_out_at"] == "", "day1's sellout on line-id 7 must not leak onto day2"


def _test_mismatched_facility_names():
    """EN 'food market' / DE 'Mensa 19' for the same facility id 19 (this
    really happened, see data/2026-08-17.jsonl) - the German join keys on
    facility id, not the display name, so it must still succeed even though
    the two sides disagree on what to call the facility."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        snapshot = {"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {
            "en": _mk_facility("19", "food market", [_mk_meal("5", "Ramen")]),
            "de": _mk_facility("19", "Mensa 19", [_mk_meal("5", "Ramen (DE)")]),
        }}
        _write_jsonl(d / "2026-08-01.jsonl", [snapshot])

        rows, _latest, _groups = build_dish_rows(d)
        row = rows[("2026-08-01", "food market", "Ramen")]
        assert row["name_de"] == "Ramen (DE)"


def _test_sold_out_in_first_snapshot():
    """A dish already sold out by the day's very first snapshot never
    appeared under a real name - it must simply be absent, not crash and
    not produce a phantom row."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        en = _mk_facility("19", "food market", [_mk_meal("3", "Sold Out")])
        _write_jsonl(d / "2026-08-01.jsonl", [{"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {"en": en}}])

        rows, _latest, _groups = build_dish_rows(d)
        assert rows == {}
        day_dish, _as_of, _fid_for, _line_for = load_day_dish(d)
        assert day_dish == {}


def _test_empty_archive():
    """No files, or a day where every dayEntry is null: must produce valid
    empty output, not raise."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        rows, latest_de_name, price_groups = build_dish_rows(d)
        assert rows == {} and latest_de_name == {} and price_groups == []

        stats = build_stats(d, latest_de_name, price_groups)
        assert stats["facilities"] == [] and stats["dishes"] == []
        assert stats["dateRange"] == {"from": None, "to": None, "days": 0}
        assert stats["overall"]["firstSeen"] is None and stats["overall"]["lastSeen"] is None

        # A day entirely present but every dayEntry null behaves the same -
        # iter_lines skips a null dayEntry, so no dish ever surfaces.
        null_day = [{"fetchedAt": "2026-08-02T06:00:00Z", "facilities": {
            "en": [{"id": "19", "name": "food market", "dayEntry": None}]}}]
        _write_jsonl(d / "2026-08-02.jsonl", null_day)
        rows2, latest2, groups2 = build_dish_rows(d)
        stats2 = build_stats(d, latest2, groups2)
        assert rows2 == {} and stats2["facilities"] == [] and stats2["dateRange"]["days"] == 0


def _test_byte_repeatability():
    """Building the same fixture twice must yield identical bytes for both
    outputs - stats.json and dishes.csv must not depend on dict/set
    iteration order or wall-clock time."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        meal = _mk_meal("1", "Ramen", **{
            "meal-class-array": [{"desc": "Meat"}],
            "meal-price-array": [{"customer-group-desc": "students", "price": 8.5}],
        })
        en = _mk_facility("19", "food market", [meal])
        _write_jsonl(d / "2026-08-01.jsonl", [{"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {"en": en}}])

        def build_once():
            rows, latest_de_name, price_groups = build_dish_rows(d)
            keys = {(r["facility"], r["name_en"]) for r in rows.values()}
            id_for, _registry = assign_dish_ids({}, keys)  # mirrors __main__'s flow
            stats = build_stats(d, latest_de_name, price_groups, id_for=id_for)
            json_bytes = (json.dumps(stats, indent=2) + "\n").encode()
            csv_path = d / "out.csv"
            write_dishes_csv(rows, stats["priceGroups"], id_for, csv_path)
            csv_bytes = csv_path.read_bytes()
            csv_path.unlink()
            return json_bytes, csv_bytes

        j1, c1 = build_once()
        j2, c2 = build_once()
        assert j1 == j2
        assert c1 == c2


def _test_dish_diet_days_matches_history():
    """Regression for the stats.html Choose-5 subtraction: a dish's
    hand-maintained meal-class tag can change over time (vegan one day,
    untagged the next). dietDays must tally each dish-day under its own
    day's classification, not just the dish's latest one - so summing it
    always equals count, with no bucket going negative when a consumer
    subtracts a dish's history out of the diet mix."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        day1 = [{"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market",
            [_mk_meal("1", "Choose 5", **{"meal-class-array": [{"desc": "Vegan"}]})])}}]
        day2 = [{"fetchedAt": "2026-08-02T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market", [_mk_meal("1", "Choose 5")])}}]  # untagged -> unclassified
        _write_jsonl(d / "2026-08-01.jsonl", day1)
        _write_jsonl(d / "2026-08-02.jsonl", day2)

        rows, latest_de_name, price_groups = build_dish_rows(d)
        stats = build_stats(d, latest_de_name, price_groups)
        dish = next(x for x in stats["dishes"] if x["name"] == "Choose 5")
        assert dish["diet"] == "unclassified"  # latest sighting only
        assert dish["dietDays"] == {"vegan": 1, "vegetarian": 0, "fish": 0,
                                     "meat": 0, "unclassified": 1}
        assert sum(dish["dietDays"].values()) == dish["count"] == 2


def _test_dish_prices_and_lines():
    """TODO items 2+3. Same fixture covers both: 'Line A' (the fixed
    counter name _mk_meal bakes in) carries two different dishes across
    three dates - Teriyaki Beef Balls on day 1 and day 3 (price changes
    between them, so sd must be non-null), Teriyaki Chicken Balls on day 2
    only (a single price observation, so sd must stay null, not 0.00)."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        def priced(line_id, name, price):
            return _mk_meal(line_id, name,
                             **{"meal-price-array": [{"customer-group-desc": "students", "price": price}]})

        day1 = [{"fetchedAt": "2026-08-03T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market", [priced("7", "Teriyaki Beef Balls", 10.50)])}}]
        day2 = [{"fetchedAt": "2026-08-04T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market", [priced("7", "Teriyaki Chicken Balls", 11.00)])}}]
        day3 = [{"fetchedAt": "2026-08-05T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market", [priced("7", "Teriyaki Beef Balls", 11.50)])}}]
        _write_jsonl(d / "2026-08-03.jsonl", day1)
        _write_jsonl(d / "2026-08-04.jsonl", day2)
        _write_jsonl(d / "2026-08-05.jsonl", day3)

        rows, latest_de_name, price_groups = build_dish_rows(d)
        stats = build_stats(d, latest_de_name, price_groups)

        beef = next(x for x in stats["dishes"] if x["name"] == "Teriyaki Beef Balls")
        assert beef["prices"]["students"]["n"] == 2
        assert beef["prices"]["students"]["sd"] == round(statistics.stdev([10.50, 11.50]), 2)
        assert beef["prices"]["students"]["mean"] == 11.0

        chicken = next(x for x in stats["dishes"] if x["name"] == "Teriyaki Chicken Balls")
        assert chicken["prices"]["students"]["n"] == 1
        assert chicken["prices"]["students"]["sd"] is None, "a single observation has no sd, not 0.00"

        line = next(l for l in stats["lines"]
                    if l["facility"] == "food market" and l["line"] == "Line A")
        assert {x["name"] for x in line["dishes"]} == {"Teriyaki Beef Balls", "Teriyaki Chicken Balls"}
        assert line["dishDays"] == 3


def _test_dish_id_new_name_gets_next_free():
    """A name with no registry entry gets a fresh id, one past the current
    max - not 1, not a reused gap."""
    registry = {1: [("food market", "Ramen")], 2: [("food market", "Pizza")]}
    id_for, new_registry = assign_dish_ids(
        registry, [("food market", "Ramen"), ("food market", "Curry")])
    assert id_for[("food market", "Curry")] == 3
    assert new_registry[3] == [("food market", "Curry")]
    assert id_for[("food market", "Ramen")] == 1  # untouched
    assert registry == {1: [("food market", "Ramen")], 2: [("food market", "Pizza")]}, \
        "assign_dish_ids must not mutate its registry argument"


def _test_dish_id_stable_across_rebuild():
    """A name already in the registry keeps its id, and re-running
    assignment over an unchanged registry+keys changes nothing - the
    no-new-ids case the workflow's commit gating depends on."""
    registry = {5: [("food market", "Ramen")]}
    id_for1, reg1 = assign_dish_ids(registry, [("food market", "Ramen")])
    id_for2, reg2 = assign_dish_ids(reg1, [("food market", "Ramen")])
    assert id_for1[("food market", "Ramen")] == 5
    assert id_for2[("food market", "Ramen")] == 5
    assert reg1 == reg2 == registry


def _test_dish_id_rename_merges_sightings():
    """The rename case: a registry entry listing two names under one id
    must merge their sightings into a single stats.json dish entry with the
    combined count, not two separate dishes that happen to share an id."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        day1 = [{"fetchedAt": "2026-08-03T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market", [_mk_meal("1", "Berlin Currywurst")])}}]
        day2 = [{"fetchedAt": "2026-08-04T06:00:00Z", "facilities": {"en": _mk_facility(
            "19", "food market", [_mk_meal("1", "Currywurst Berliner Art")])}}]
        _write_jsonl(d / "2026-08-03.jsonl", day1)
        _write_jsonl(d / "2026-08-04.jsonl", day2)

        rows, latest_de_name, price_groups = build_dish_rows(d)
        registry = {7: [("food market", "Berlin Currywurst"),
                         ("food market", "Currywurst Berliner Art")]}
        keys = {(r["facility"], r["name_en"]) for r in rows.values()}
        id_for, _new_registry = assign_dish_ids(registry, keys)
        assert id_for[("food market", "Berlin Currywurst")] == 7
        assert id_for[("food market", "Currywurst Berliner Art")] == 7

        stats = build_stats(d, latest_de_name, price_groups, id_for=id_for)
        assert len(stats["dishes"]) == 1, "a recorded rename must merge into one dish, not two"
        dish = stats["dishes"][0]
        assert dish["id"] == 7
        assert dish["count"] == 2
        assert dish["dates"] == ["2026-08-03", "2026-08-04"]
        assert dish["name"] == "Currywurst Berliner Art", "latest sighting's name wins, like nameDe elsewhere"

        for r in rows.values():
            assert id_for[(r["facility"], r["name_en"])] == 7  # dishes.csv agrees too


def _test_dish_id_allocation_deterministic_for_batch():
    """Several brand-new names allocated in one call must get the same ids
    regardless of the order they're passed in - sorted (facility, name)
    order is the only thing that may decide allocation order, not dict/set
    iteration, or two builds over the same archive could assign different
    ids and churn stats.json every run."""
    keys = [("food market", "Zucchini"), ("food market", "Apple"),
            ("Mendokoro", "Apple"), ("food market", "Mango")]
    id_for1, reg1 = assign_dish_ids({}, keys)
    id_for2, reg2 = assign_dish_ids({}, list(reversed(keys)))
    assert id_for1 == id_for2
    assert reg1 == reg2
    # sorted (facility, name): uppercase "Mendokoro" sorts before lowercase
    # "food market" (ASCII), so this is not alphabetical-by-dish-name-alone.
    assert id_for1[("Mendokoro", "Apple")] == 1
    assert id_for1[("food market", "Apple")] == 2
    assert id_for1[("food market", "Mango")] == 3
    assert id_for1[("food market", "Zucchini")] == 4


def _test_dish_id_registry_reserialize_byte_identical():
    """Loading a registry and re-serialising it without allocating anything
    new must produce byte-identical output - the property the workflow's
    commit-only-when-changed gating relies on for a no-op run."""
    with tempfile.TemporaryDirectory() as td:
        path = Path(td) / "dish-ids.json"
        registry = {1: [("food market", "Ramen")], 2: [("Mendokoro", "Sushi")]}
        write_dish_ids(registry, path)
        b1 = path.read_bytes()
        loaded = load_dish_ids(path)
        assert loaded == registry
        write_dish_ids(loaded, path)
        assert path.read_bytes() == b1


def _test_facility_order_from_websites_txt():
    """A fixture whose websites.txt order differs from alphabetical must
    come out in file order, and a missing websites.txt must raise rather
    than silently degrading to alphabetical order (see parse_websites_order
    for why a silent degrade is the wrong call here)."""
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        websites = d / "websites.txt"
        # File order is 20, 19 - the reverse of alphabetical name order, so
        # a passing test proves file order wins, not name order.
        websites.write_text(
            "https://example.org/offerDay.html?id=20&date=2026-08-03\n"
            "https://example.org/offerDay.html?id=19&date=2026-08-03\n"
        )
        assert parse_websites_order(websites) == ["20", "19"]

        en = (_mk_facility("19", "Alpha", [_mk_meal("1", "Dish Alpha")])
              + _mk_facility("20", "Zeta", [_mk_meal("2", "Dish Zeta")])
              + _mk_facility("99", "Unlisted", [_mk_meal("3", "Dish Unlisted")]))
        _write_jsonl(d / "2026-08-01.jsonl", [{"fetchedAt": "2026-08-01T06:00:00Z", "facilities": {"en": en}}])

        rows, latest_de_name, price_groups = build_dish_rows(d)
        stats = build_stats(d, latest_de_name, price_groups, websites_path=websites)
        names = [f["name"] for f in stats["facilities"]]
        assert names == ["Zeta", "Alpha", "Unlisted"], names

        try:
            build_stats(d, latest_de_name, price_groups,
                        websites_path=d / "does-not-exist.txt")
            assert False, "missing websites.txt must raise, not degrade to alphabetical order"
        except OSError:
            pass


def _extract_js_list(text, const_name, source):
    """Pull the quoted strings out of a `const NAME = [...]` JS array."""
    m = re.search(rf"const {const_name}\s*=\s*\[(.*?)\]\s*;", text, re.DOTALL)
    assert m, (f"{const_name} not found in {source} - it was moved, renamed "
               f"or restructured, so _test_policy_matches_html_pages() can no "
               f"longer check it against build_stats.py; fix the check and "
               f"re-verify the policies still agree before relying on it again")
    return [a or b for a, b in re.findall(r"'([^']*)'|\"([^\"]*)\"", m.group(1))]


def _extract_js_string(text, const_name, source):
    """Pull the quoted value out of a `const NAME = '...'` JS assignment."""
    m = re.search(rf"const {const_name}\s*=\s*'([^']*)'\s*;", text)
    assert m, (f"{const_name} not found in {source} - it was moved, renamed "
               f"or restructured, so _test_policy_matches_html_pages() can no "
               f"longer check it against build_stats.py; fix the check and "
               f"re-verify the policies still agree before relying on it again")
    return m.group(1)


def _test_policy_matches_html_pages(index_html_path=INDEX_HTML_PATH,
                                     stats_html_path=STATS_HTML_PATH):
    """Drift guard for TODO item 7: diet precedence, the sold-out labels and
    the build-your-own name are deliberately duplicated in build_stats.py,
    index.html and stats.html rather than unified into one runtime-fetched
    config (that would put a new failure mode on the menu page's render
    path to save three small constants). The risk that duplication carries
    is silent drift - a copy changes and the pages quietly disagree, which
    has already happened twice here before facility order and price-group
    order were each unified onto one canonical source. This test removes
    that risk without removing the duplication: it reads index.html's and
    stats.html's actual source off disk - unlike every other check in this
    file, which builds synthetic fixtures - because the point is to catch
    the real pages drifting, not a fixture that always agrees with itself.
    Extraction failures are hard asserts, not skips: a silent skip after
    someone restructures the JS would defeat the entire guard."""
    index_src = index_html_path.read_text()
    stats_src = stats_html_path.read_text()

    # Sold-out placeholders: build_stats.py's SOLD_OUT vs index.html's
    # SOLD_OUT_LABELS must name the same placeholders, case-insensitively.
    # (stats.html has no equivalent list to check - it never inspects a
    # meal's sold-out state itself.)
    js_sold_out = {s.lower() for s in _extract_js_list(index_src, "SOLD_OUT_LABELS", "index.html")}
    assert js_sold_out == SOLD_OUT, (
        f"SOLD_OUT in build_stats.py ({sorted(SOLD_OUT)}) and SOLD_OUT_LABELS "
        f"in index.html ({sorted(js_sold_out)}) disagree - change both together")

    # Diet precedence: index.html's classifyMeal() must check vegan before
    # vegetarian, matching DIET_PRECEDENCE's best-wins order, and its label
    # lists must include the exact API desc words build_stats.py matches on.
    m = re.search(r"function classifyMeal\(meal\)\s*\{.*?\n\}", index_src, re.DOTALL)
    assert m, ("classifyMeal() not found in index.html - update this check "
               "together with it")
    body = m.group(0)
    assert "VEGAN_LABELS" in body and "VEGETARIAN_LABELS" in body, (
        "classifyMeal() in index.html no longer references VEGAN_LABELS/"
        "VEGETARIAN_LABELS - update this check together with it")
    vegan_idx = DIET_PRECEDENCE.index("vegan")
    vegetarian_idx = DIET_PRECEDENCE.index("vegetarian")
    js_checks_vegan_first = body.index("VEGAN_LABELS") < body.index("VEGETARIAN_LABELS")
    assert (vegan_idx < vegetarian_idx) == js_checks_vegan_first, (
        "DIET_PRECEDENCE in build_stats.py and the check order in index.html's "
        "classifyMeal() disagree on whether vegan or vegetarian wins when a "
        "dish carries both tags - reorder one to match the other")
    vegan_labels = _extract_js_list(index_src, "VEGAN_LABELS", "index.html")
    vegetarian_labels = _extract_js_list(index_src, "VEGETARIAN_LABELS", "index.html")
    assert DIET_PRECEDENCE[vegan_idx].capitalize() in vegan_labels, (
        f"DIET_PRECEDENCE has {DIET_PRECEDENCE[vegan_idx]!r} but index.html's "
        f"VEGAN_LABELS {vegan_labels} has no matching API desc word - "
        f"change both together")
    assert DIET_PRECEDENCE[vegetarian_idx].capitalize() in vegetarian_labels, (
        f"DIET_PRECEDENCE has {DIET_PRECEDENCE[vegetarian_idx]!r} but "
        f"index.html's VEGETARIAN_LABELS {vegetarian_labels} has no matching "
        f"API desc word - change both together")

    # Build-your-own exclusion: stats.html's CHOOSE_5_NAME and index.html's
    # special-casing in mealSortKey() must name the same dish.
    choose5_stats = _extract_js_string(stats_src, "CHOOSE_5_NAME", "stats.html")
    m = re.search(r"meal\.name === '([^']*)'", index_src)
    assert m, ("mealSortKey()'s build-your-own special case not found in "
               "index.html - update this check together with it")
    choose5_index = m.group(1)
    assert choose5_stats == choose5_index, (
        f"CHOOSE_5_NAME in stats.html ({choose5_stats!r}) and the "
        f"build-your-own name in index.html's mealSortKey() ({choose5_index!r}) "
        f"disagree - change both together")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/stats.json", help="output path")
    parser.add_argument("--out-csv", default="data/dishes.csv", help="tidy per-dish-day csv output path")
    parser.add_argument("--data-dir", default="data", help="directory of *.jsonl snapshots")
    parser.add_argument("--allow-new-registry", action="store_true",
                        help="create data/dish-ids.json if absent (bootstrap only; "
                             "CI must never pass this - see load_dish_ids)")
    parser.add_argument("--dish-ids", default=str(DISH_IDS_PATH),
                         help="numeric dish-id registry path (TODO item 8) - read and rewritten every build")
    parser.add_argument("--selftest", action="store_true", help="run self checks and exit")
    args = parser.parse_args()

    if args.selftest:
        demo()
    else:
        rows, latest_de_name, price_groups = build_dish_rows(Path(args.data_dir))

        # Registry allocation happens once, here, and the resulting id_for is
        # threaded into both outputs below - build_stats and write_dishes_csv
        # each parse the archive their own way (deduped-by-day vs per-date
        # rows), so computing ids independently in each risks the two
        # disagreeing; a single shared mapping can't.
        dish_ids_path = Path(args.dish_ids)
        keys = {(r["facility"], r["name_en"]) for r in rows.values()}
        old_registry = load_dish_ids(dish_ids_path, allow_new=args.allow_new_registry)
        already_known = {pair for names in old_registry.values() for pair in names}
        id_for, dish_id_registry = assign_dish_ids(old_registry, keys)
        write_dish_ids(dish_id_registry, dish_ids_path)

        stats = build_stats(Path(args.data_dir), latest_de_name, price_groups, id_for=id_for)
        Path(args.out).write_text(json.dumps(stats, indent=2) + "\n")
        write_dishes_csv(rows, stats["priceGroups"], id_for, Path(args.out_csv))
        print(f"wrote {args.out}: {len(stats['facilities'])} facilities, "
              f"{len(stats['dishes'])} dishes, "
              f"{sum(1 for d in stats['dishes'] if d['prediction'])} with a prediction")
        print(f"wrote {args.out_csv}: {len(rows)} dish-day rows, "
              f"{sum(1 for r in rows.values() if r['sold_out_at'])} sold out")
        print(f"wrote {dish_ids_path}: {len(dish_id_registry)} ids "
              f"({len(keys - already_known)} newly allocated this build)")
