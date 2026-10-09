"""Read YOUR 14-day traffic CSV for the street and turn it into street_history.csv for predict.py.

    python import_csv.py street_data.csv                 # then: python predict.py
    python import_csv.py street_data.csv --key Street    # name used for this street in the forecast

Works with most layouts; it finds the columns by their names:
  time    : one "timestamp"/"datetime" column, OR "date" + "time" (or "hour") columns
  count   : "count"/"vehicles"/"total"/"volume"/"flow" column, OR per-type columns
            (car, bike, motorcycle, two_wheeler, auto, bus, truck ...) which are added up
  street  : optional "location"/"street"/"road"/"junction"/"camera" column (one series per street)
Rows can be every 1, 5, 15, 30 or 60 minutes; everything is turned into 15-minute counts.
"""
import argparse
import csv
import datetime as dt
import os
import statistics
from collections import defaultdict

SLOT_MIN = 15
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "street_history.csv")
DATE_FMTS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d.%m.%Y", "%d-%b-%Y", "%d %b %Y",
             "%d-%m-%y", "%d/%m/%y")
TIME_FMTS = ("%H:%M", "%H:%M:%S", "%I:%M %p", "%I:%M:%S %p", "%I %p", "%H")
TS_NAMES = ("timestamp", "datetime", "date_time", "date time", "time_stamp")
DATE_NAMES = ("date", "day_date")
TIME_NAMES = ("time", "time_slot", "slot", "hour", "hr", "start_time", "from")
COUNT_NAMES = ("count", "vehicles", "vehicle_count", "total", "total_vehicles", "volume", "traffic",
               "flow", "no_of_vehicles", "num_vehicles", "vehicle count", "total vehicles")
TYPE_NAMES = ("car", "cars", "bike", "bikes", "motorcycle", "motorcycles", "two_wheeler", "two wheeler",
              "2w", "scooter", "auto", "autos", "auto_rickshaw", "rickshaw", "3w", "bus", "buses",
              "truck", "trucks", "lorry", "van", "jeep", "taxi", "bicycle", "tempo", "minibus")
KEY_NAMES = ("location", "street", "road", "junction", "camera", "place", "site", "key", "area")
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def norm(h):
    return h.strip().lower().replace("-", "_")


def find(headers, names):
    for n in names:
        for h in headers:
            if norm(h) == n or norm(h).replace("_", " ") == n:
                return h
    for n in names:                                  # looser: "vehicle_count_total" contains "count"
        for h in headers:
            if n in norm(h) and len(n) > 3:
                return h
    return None


def parse_date(s):
    s = s.strip()
    for f in DATE_FMTS:
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            pass
    return None


def parse_time(s):
    s = s.strip().upper().replace(".", ":")
    if "-" in s and ":" in s:                        # "07:00-07:15" -> start of the slot
        s = s.split("-")[0].strip()
    for f in TIME_FMTS:
        try:
            t = dt.datetime.strptime(s, f)
            return t.hour * 60 + t.minute
        except ValueError:
            pass
    return None


def parse_ts(s):
    s = s.strip().replace("T", " ")
    for cut in (19, 16):
        try:
            t = dt.datetime.fromisoformat(s[:cut])
            return t.date(), t.hour * 60 + t.minute
        except ValueError:
            pass
    parts = s.split(" ", 1)
    if len(parts) == 2:
        d, m = parse_date(parts[0]), parse_time(parts[1])
        if d and m is not None:
            return d, m
    return None, None


def number(v):
    try:
        return float(str(v).replace(",", "").strip() or 0)
    except ValueError:
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--key", default="Street", help="name of this street in the forecast")
    a = ap.parse_args()

    with open(a.csv, newline="", encoding="utf-8-sig") as f:
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows = list(csv.DictReader(f, dialect=dialect))
    if not rows:
        raise SystemExit("The CSV is empty.")
    headers = list(rows[0].keys())
    print("Columns found:", ", ".join(headers))

    ts_col = find(headers, TS_NAMES)
    date_col = None if ts_col else find(headers, DATE_NAMES)
    time_col = None if ts_col else find(headers, TIME_NAMES)
    key_col = find(headers, KEY_NAMES)
    count_col = find(headers, COUNT_NAMES)
    type_cols = [] if count_col else [h for h in headers if norm(h) in TYPE_NAMES or norm(h).replace("_", " ") in TYPE_NAMES]
    if not count_col and not type_cols:                # any other numeric column
        skip = {ts_col, date_col, time_col, key_col}
        type_cols = [h for h in headers if h not in skip and number(rows[0][h]) is not None]
    if not (ts_col or (date_col and time_col)):
        raise SystemExit("Could not find the date/time columns. Rename them to 'date' and 'time' "
                         "(or one 'timestamp' column) and run again.")
    if not (count_col or type_cols):
        raise SystemExit("Could not find the vehicle count column. Rename it to 'count' and run again.")
    print(f"Using  time: {ts_col or (date_col + ' + ' + time_col)}   count: {count_col or ' + '.join(type_cols)}"
          f"   street: {key_col or a.key}")

    # ---- read every row -> (key, date, minute) : vehicles
    pts, bad = defaultdict(float), 0
    for r in rows:
        if ts_col:
            d, m = parse_ts(r[ts_col])
        else:
            d, m = parse_date(r[date_col]), parse_time(r[time_col])
        n = number(r[count_col]) if count_col else sum(number(r[c]) or 0 for c in type_cols)
        if d is None or m is None or n is None:
            bad += 1
            continue
        key = (r[key_col].strip() if key_col and r[key_col].strip() else a.key).replace(",", " ")
        pts[(key, d, m)] += n
    if not pts:
        raise SystemExit("No row could be read. Check the date/time format (e.g. 2026-09-21 and 07:15).")
    if bad:
        print(f"Skipped {bad} rows that could not be read.")

    # ---- interval of the data (minutes between rows), then spread into 15-minute slots
    by_day = defaultdict(list)
    for key, d, m in pts:
        by_day[(key, d)].append(m)
    gaps = [b - a_ for ms in by_day.values() for a_, b in zip(sorted(ms), sorted(ms)[1:]) if b > a_]
    step = int(statistics.median(gaps)) if gaps else 60
    step = min(max(step, 1), 60)
    print(f"Rows are every {step} min -> converted to {SLOT_MIN}-minute counts")
    slots = defaultdict(float)
    for (key, d, m), n in pts.items():
        if step >= SLOT_MIN:                          # one row covers several 15-min slots: share it
            k = step // SLOT_MIN
            for i in range(k):
                slots[(key, d, ((m + i * SLOT_MIN) // SLOT_MIN) % (1440 // SLOT_MIN))] += n / k
        else:
            slots[(key, d, m // SLOT_MIN)] += n

    present = {(k, d) for k, d, _ in slots}
    keys = sorted({k for k, _, _ in slots})
    dates = sorted({d for _, d, _ in slots})
    with open(OUT, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "day", "time", "key", "count"])
        for key in keys:
            for d in dates:
                if (key, d) not in present:
                    continue
                for s in range(1440 // SLOT_MIN):
                    if True:
                        m = s * SLOT_MIN
                        w.writerow([d.isoformat(), WEEKDAYS[d.weekday()], f"{m // 60:02d}:{m % 60:02d}", key,
                                    round(slots.get((key, d, s), 0.0), 2)])
    print(f"Saved street_history.csv: {len(dates)} days ({dates[0]} to {dates[-1]}), streets: {', '.join(keys)}")
    if len(dates) < 7:
        print("Note: less than 7 days - weekday/weekend patterns will be weak.")
    print("Next:  python predict.py")


if __name__ == "__main__":
    main()
