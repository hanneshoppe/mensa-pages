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
    """Yield (facility_name, facility_id, dish_name, meal) for every real
    dish of the day."""
    for facility, fid, _line, meal in iter_lines(facilities_en):
        name = meal.get("name")
        if name is None or name.strip().lower() in SOLD_OUT:
            continue
        yield facility, fid, name, meal


def load_day_dish(data_dir):
    """Read every data/*.jsonl file and dedup down to one meal per
    (date, facility, dish name), keeping the most recent snapshot line seen
    for that day (multiple snapshot lines per day repeat the same dishes).

    Also returns facility_id_for: facility_name -> id (first seen), so
    build_stats can order facilities[] by websites.txt position without a
    second pass over the archive."""
    day_dish = {}
    facility_id_for = {}
    latest_fetched_at = None  # max fetchedAt seen, for a deterministic "as of"
    for path in sorted(glob.glob(str(data_dir / "*.jsonl"))):
        d = Path(path).stem
        try:
            date.fromisoformat(d)
        except ValueError:
            continue
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                snapshot = json.loads(line)
                fetched_at = snapshot.get("fetchedAt")
                if fetched_at and (latest_fetched_at is None or fetched_at > latest_fetched_at):
                    latest_fetched_at = fetched_at
                for facility, fid, dish, meal in iter_meals(snapshot["facilities"]["en"]):
                    day_dish[(d, facility, dish)] = meal
                    facility_id_for.setdefault(facility, fid)
    return day_dish, latest_fetched_at, facility_id_for


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


def write_dishes_csv(rows, price_groups, out_path):
    fixed = ["date", "facility", "facility_id", "line", "name_en", "name_de", "diet",
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
            out = {k: row[k] for k in fixed}
            for g in price_groups:
                out[f"price_{g}"] = row["prices"].get(g, "")
            out.update(row["nutrition"])
            writer.writerow(out)


def build_stats(data_dir, latest_de_name=None, price_groups=None, websites_path=WEBSITES_PATH):
    day_dish, data_as_of, facility_id_for = load_day_dish(data_dir)

    facility_days = defaultdict(set)
    facility_dishdays = Counter()
    facility_diet = defaultdict(Counter)
    facility_prices = defaultdict(lambda: defaultdict(list))

    overall_days = set()
    overall_diet = Counter()
    overall_prices = defaultdict(list)

    by_dish_dates = defaultdict(list)  # (facility, dish) -> dates, ascending
    by_dish_latest_meal = {}  # (facility, dish) -> most recent meal seen
    by_dish_diet = defaultdict(Counter)  # (facility, dish) -> Counter of each dish-day's own diet

    for (d, facility, dish), meal in sorted(day_dish.items()):
        diet = classify_diet(meal.get("meal-class-array"))

        facility_days[facility].add(d)
        facility_dishdays[facility] += 1
        facility_diet[facility][diet] += 1
        overall_days.add(d)
        overall_diet[diet] += 1
        by_dish_diet[(facility, dish)][diet] += 1

        for p in meal.get("meal-price-array") or []:
            group, price = p.get("customer-group-desc"), p.get("price")
            if group is None or price is None:
                continue
            facility_prices[facility][group].append(price)
            overall_prices[group].append(price)

        by_dish_dates[(facility, dish)].append(d)
        by_dish_latest_meal[(facility, dish)] = meal  # last write = most recent (sorted by date)

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
    for (facility, dish), dates_seen in by_dish_dates.items():
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

        meal = by_dish_latest_meal[(facility, dish)]
        dishes.append({
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
            "dietDays": {k: by_dish_diet[(facility, dish)].get(k, 0) for k in DIET_KEYS},
            "count": len(dates_seen),
            "firstSeen": dates_seen[0],
            "lastSeen": dates_seen[-1],
            "dates": dates_seen,
            "gapsWeekdays": gaps,
            "meanGapWeekdays": mean_gap,
            "sdGapWeekdays": sd_gap,
            "prediction": prediction,
        })
    dishes.sort(key=lambda x: (-x["count"], x["name"]))

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

        day_dish, _as_of, _fid_for = load_day_dish(d)
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
        day_dish, _as_of, _fid_for = load_day_dish(d)
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
            stats = build_stats(d, latest_de_name, price_groups)
            json_bytes = (json.dumps(stats, indent=2) + "\n").encode()
            csv_path = d / "out.csv"
            write_dishes_csv(rows, stats["priceGroups"], csv_path)
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="data/stats.json", help="output path")
    parser.add_argument("--out-csv", default="data/dishes.csv", help="tidy per-dish-day csv output path")
    parser.add_argument("--data-dir", default="data", help="directory of *.jsonl snapshots")
    parser.add_argument("--selftest", action="store_true", help="run self checks and exit")
    args = parser.parse_args()

    if args.selftest:
        demo()
    else:
        rows, latest_de_name, price_groups = build_dish_rows(Path(args.data_dir))
        stats = build_stats(Path(args.data_dir), latest_de_name, price_groups)
        Path(args.out).write_text(json.dumps(stats, indent=2) + "\n")
        write_dishes_csv(rows, stats["priceGroups"], Path(args.out_csv))
        print(f"wrote {args.out}: {len(stats['facilities'])} facilities, "
              f"{len(stats['dishes'])} dishes, "
              f"{sum(1 for d in stats['dishes'] if d['prediction'])} with a prediction")
        print(f"wrote {args.out_csv}: {len(rows)} dish-day rows, "
              f"{sum(1 for r in rows.values() if r['sold_out_at'])} sold out")
