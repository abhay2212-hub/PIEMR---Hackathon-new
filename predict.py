"""Learn the daily pattern from the history and predict school / office rush times.

    python predict.py      -> forecast.json (used by: python city.py smart --predict)
                              prediction.png (predicted vs real, for the judges)

Uses street_history.csv (YOUR 14-day street data, from import_csv.py) if it exists,
otherwise history.csv (simulated, from history.py), plus yolo_history.csv (real camera counts,
written automatically by city.py --yolo).

The rush times found in the street data are then EXPLAINED with everyday city knowledge
(school opening / closing, office arrival / leaving) and given to the School / Office roads,
so city.py --predict can divert vehicles BEFORE the jam builds up, on those days only.
"""
import csv
import json
import os
from collections import defaultdict

SLOT_MIN = 15
SLOTS = 24 * 60 // SLOT_MIN
TEST_DAYS = 3
DAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
PLACE_KEYS = ("School", "Office", "Hospital", "Main")       # names used by history.py / the city

# Why a street gets busy at a certain time (typical Indian city).  (cause, place, days, from, to)
CAUSES = [
    ("School opening",          "School", DAYS[:6], "06:45", "08:40"),
    ("School closing",          "School", DAYS[:5], "12:15", "15:45"),
    ("School closing (Sat)",    "School", ("Sat",), "10:45", "13:15"),
    ("Office arrival",          "Office", DAYS[:6], "08:45", "11:15"),
    ("Office leaving",          "Office", DAYS[:6], "16:45", "20:45"),
]


def daytype(day):
    return 0 if day in DAYS[:5] else (1 if day == "Sat" else 2)


def load():
    data = defaultdict(float)                      # (date, day, slot, key) -> count
    main = "street_history.csv" if os.path.exists("street_history.csv") else "history.csv"
    for path in (main, "yolo_history.csv"):
        if not os.path.exists(path):
            continue
        with open(path) as f:
            for r in csv.DictReader(f):
                h, m = r["time"].split(":")
                slot = (int(h) * 60 + int(m)) // SLOT_MIN
                data[(r["date"], r["day"], slot, r["key"])] += float(r["count"])
        print("read", path)
    if not data:
        raise SystemExit("No history found. Run: python history.py")
    return [(d, day, s, k, c) for (d, day, s, k), c in data.items()]


class Model:
    """Gradient boosting (scikit-learn) if installed, otherwise average of same day-type + time."""

    def __init__(self, keys):
        self.keys = sorted(keys)
        try:
            from sklearn.ensemble import HistGradientBoostingRegressor
            self.ml = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08,
                                                    categorical_features=[0, 1], random_state=0)
            self.name = "gradient boosting (scikit-learn)"
        except ImportError:
            self.ml = None
            self.name = "average of same day-type and time"

    def x(self, day, slot, key):
        return [daytype(day), self.keys.index(key), slot, slot // 4]

    def fit(self, rows):
        if self.ml is not None:
            import numpy as np
            self.ml.fit(np.array([self.x(day, s, k) for _, day, s, k, _ in rows], dtype=float),
                        np.array([c for *_, c in rows], dtype=float))
        else:
            acc = defaultdict(list)
            for _, day, s, k, c in rows:
                acc[(daytype(day), s, k)].append(c)
            self.mean = {key: sum(v) / len(v) for key, v in acc.items()}
        return self

    def predict(self, items):
        if self.ml is not None:
            import numpy as np
            X = np.array([self.x(d, s, k) for d, s, k in items], dtype=float)
            return [max(0.0, float(v)) for v in self.ml.predict(X)]
        return [self.mean.get((daytype(d), s, k), 0.0) for d, s, k in items]


def peaks(series, relative=False):
    """Rush windows: slots clearly above the normal level of that road.
    relative=True (your street data, any scale): above normal AND in the top part of the day's range."""
    srt = sorted(series)
    normal = srt[len(srt) // 2]
    if relative:
        top = srt[-1]
        hot = [v > normal * 1.3 and v >= normal + 0.35 * (top - normal) and top > 0 for v in series]
    else:
        hot = [v > max(normal * 1.6, normal + 30) for v in series]
    for i in range(1, len(hot) - 2):               # join peaks with a gap of up to 30 min
        if hot[i - 1] and not hot[i] and (hot[i + 1] or hot[i + 2]):
            hot[i] = True
            if not hot[i + 1]:
                hot[i + 1] = True
    out, i = [], 0
    while i < len(hot):
        if hot[i]:
            j = i
            while j + 1 < len(hot) and hot[j + 1]:
                j += 1
            a, b = i * SLOT_MIN, (j + 1) * SLOT_MIN
            out.append((f"{a // 60:02d}:{a % 60:02d}", f"{min(b, 1439) // 60:02d}:{min(b, 1439) % 60:02d}"))
            i = j + 1
        else:
            i += 1
    return out


def mins(hm):
    h, m = hm.split(":")
    return int(h) * 60 + int(m)


def hm(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def explain(day, a, b):
    """Which everyday causes explain the rush a-b? A long rush can have two causes
    (school opening + office arrival). -> [(cause, place, from, to)]"""
    out = []
    for cause, place, days, c0, c1 in CAUSES:
        if day not in days:
            continue
        s0, s1 = max(mins(a), mins(c0)), min(mins(b), mins(c1))
        if s1 - s0 >= 30:                              # at least 30 min of this rush fits the cause
            out.append((cause, place, hm(s0), hm(s1)))
    return out or [("Other rush", None, a, b)]


def main():
    rows = load()
    keys = {k for *_, k, _ in rows}
    dates = sorted({d for d, *_ in rows})
    test_dates = set(dates[-TEST_DAYS:])
    train = [r for r in rows if r[0] not in test_dates]
    test = sorted([r for r in rows if r[0] in test_dates], key=lambda r: (r[0], r[3], r[2]))

    # ---- test on days the model has NOT seen
    m = Model(keys).fit(train)
    pred = m.predict([(day, s, k) for _, day, s, k, _ in test])
    err = sum(abs(p - r[4]) for p, r in zip(pred, test))
    tot = sum(r[4] for r in test) or 1
    print(f"Model: {m.name}")
    print(f"Test on last {TEST_DAYS} days: average error {err / len(test):.1f} veh per 15 min, "
          f"accuracy {100 * (1 - err / tot):.1f} %")

    # ---- final model on all data -> forecast for every day of the week
    m = Model(keys).fit(rows)
    fc = {"slot_min": SLOT_MIN, "model": m.name, "accuracy_pct": round(100 * (1 - err / tot), 1),
          "days": {}, "peaks": {}}
    for day in DAYS:
        fc["days"][day], fc["peaks"][day] = {}, {}
        for k in sorted(keys):
            series = m.predict([(day, s, k) for s in range(SLOTS)])
            fc["days"][day][k] = [round(v, 1) for v in series]
            fc["peaks"][day][k] = peaks(series, relative=k not in PLACE_KEYS)
    # ---- explain the rush times of YOUR street(s) and give them to the School / Office roads
    fc["causes"] = {}
    for day in DAYS:
        fc["causes"][day] = []
        for k in sorted(keys):
            if k in PLACE_KEYS:
                continue
            for a0, b0 in fc["peaks"][day][k]:
                for cause, place, a, b in explain(day, a0, b0):
                    fc["causes"][day].append({"street": k, "from": a, "to": b, "cause": cause, "place": place})
                    if place:
                        w = fc["peaks"][day].setdefault(place, [])
                        if [a, b] not in w and (a, b) not in w:
                            w.append((a, b))
                            w.sort()
    with open("forecast.json", "w") as f:
        json.dump(fc, f)
    print("forecast.json saved. Predicted rush times on a weekday (Mon):")
    for k, w in fc["peaks"]["Mon"].items():
        print(f"  {k:9s} " + (", ".join(f"{a}-{b}" for a, b in w) or "no rush"))
    if any(fc["causes"].values()):
        print("\nWhy your street gets busy (diversions are planned for these):")
        for day in DAYS:
            for c in fc["causes"][day]:
                print(f"  {day}  {c['from']}-{c['to']}  {c['street']}: {c['cause']}"
                      + (f"  -> divert around the {c['place']} road" if c["place"] else "  (no diversion)"))

    # ---- chart: predicted vs real on the test days
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("pip install matplotlib for prediction.png")
        return
    show = [k for k in sorted(keys) if k not in PLACE_KEYS][:2] or [k for k in ("School", "Office") if k in keys] \
        or sorted(keys)[:2]
    fig, ax = plt.subplots(len(show), 1, figsize=(14, 4 * len(show)), squeeze=False)
    for a, k in zip(ax[:, 0], show):
        rk = [(r, p) for r, p in zip(test, pred) if r[3] == k]
        x = [i * SLOT_MIN / 60 for i in range(len(rk))]
        a.plot(x, [r[4] for r, _ in rk], color="#999", label="real (not seen by the model)")
        a.plot(x, [p for _, p in rk], color="#1e8e3e", lw=2, label="predicted")
        for i, d in enumerate(sorted(test_dates)):
            a.axvline(i * 24, color="#ddd")
            day = next(r[1] for r in test if r[0] == d)
            a.text(i * 24 + 12, a.get_ylim()[1] * 0.92, f"{day} {d[5:]}", ha="center")
        a.set_title(f"{k} road: predicted vs real")
        a.set_xlabel("hours")
        a.set_ylabel("vehicles / 15 min")
        a.legend(loc="center right")
    fig.suptitle(f"Prediction test - accuracy {fc['accuracy_pct']} %", weight="bold")
    plt.tight_layout()
    plt.savefig("prediction.png", dpi=130)
    print("prediction.png saved")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
