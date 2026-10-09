"""Create 2 weeks of (simulated) traffic history: school start/dispersal, office peaks, hospital.

    python history.py      -> history.csv + history.png

Counts are vehicles per 15 minutes on the road beside each place, and on a main road.
In a real deployment this file is filled by the camera (YOLO) every day instead.
"""
import csv
import datetime as dt
import random

DAYS = 14
START = dt.date(2026, 9, 21)          # a Monday
SLOT_MIN = 15
SEED = 3
KEYS = ("School", "Office", "Hospital", "Main")

rng = random.Random(SEED)
HOLIDAY = START + dt.timedelta(days=9)        # a Wednesday holiday: no school, no office
LATE_DAY = START + dt.timedelta(days=3)       # school dispersal 30 min late (exam day)
RAIN_DAY = START + dt.timedelta(days=11)      # rain: everything 20 % busier


def bump(m, start, end, height, ramp=15):
    """Smooth peak between start and end (minutes of the day)."""
    if m < start - ramp or m > end + ramp:
        return 0.0
    if m < start:
        return height * (m - (start - ramp)) / ramp
    if m > end:
        return height * ((end + ramp) - m) / ramp
    return height


def base(m):
    """Normal city traffic over the day (veh / 15 min)."""
    h = m / 60
    if h < 6:
        return 8
    return 20 + bump(m, 8 * 60, 11 * 60, 35, 90) + bump(m, 17 * 60, 20 * 60, 45, 90) + 15


def expected(key, day, m):
    wd = day.weekday()                      # 0 = Monday
    holiday = day == HOLIDAY
    v = base(m)
    if key == "School":
        v *= 0.4
        if not holiday and wd < 5:
            disp = 13 * 60 + 30 + (30 if day == LATE_DAY else 0)
            v += bump(m, 7 * 60 + 30, 8 * 60 + 15, 120) + bump(m, disp, disp + 45, 110)
        elif not holiday and wd == 5:                         # Saturday half day
            v += bump(m, 7 * 60 + 30, 8 * 60 + 15, 100) + bump(m, 11 * 60 + 30, 12 * 60 + 15, 90)
    elif key == "Office":
        v *= 0.6
        if not holiday and wd < 6:
            k = 1.0 if wd < 5 else 0.5
            v += k * (bump(m, 9 * 60, 10 * 60, 100) + bump(m, 18 * 60, 19 * 60, 110))
    elif key == "Hospital":
        v = 30 + bump(m, 9 * 60, 12 * 60, 30, 30) + (0 if m > 6 * 60 else -15)
    elif key == "Main":
        v *= 1.6
        if wd >= 5:
            v *= 0.8
    if day == RAIN_DAY:
        v *= 1.2
    return max(v, 0)


def main():
    rows = []
    for d in range(DAYS):
        day = START + dt.timedelta(days=d)
        for slot in range(24 * 60 // SLOT_MIN):
            m = slot * SLOT_MIN
            for key in KEYS:
                mu = expected(key, day, m)
                count = max(0, round(rng.gauss(mu, 3 + 0.12 * mu)))
                rows.append([day.isoformat(), day.strftime("%a"), f"{m // 60:02d}:{m % 60:02d}", key, count])
    with open("history.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["date", "day", "time", "key", "count"])
        w.writerows(rows)
    print(f"history.csv: {DAYS} days x {len(KEYS)} roads, {len(rows)} rows "
          f"(holiday {HOLIDAY}, late dispersal {LATE_DAY}, rain {RAIN_DAY})")

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("pip install matplotlib for history.png")
        return
    fig, ax = plt.subplots(figsize=(15, 5))
    colors = {"School": "#e6a700", "Office": "#3c78ff", "Hospital": "#e62828", "Main": "#888888"}
    for key in KEYS:
        r = [x for x in rows if x[3] == key]
        ax.plot([i * SLOT_MIN / 60 / 24 for i in range(len(r))], [x[4] for x in r],
                label=key, color=colors[key], lw=1)
    for d in range(DAYS):
        day = START + dt.timedelta(days=d)
        ax.text(d + 0.5, ax.get_ylim()[1] * 0.97, day.strftime("%a %d"), ha="center", fontsize=8)
        ax.axvline(d, color="#ddd", lw=0.8)
    ax.set_xlim(0, DAYS)
    ax.set_xlabel("days")
    ax.set_ylabel("vehicles per 15 min")
    ax.set_title("2 weeks of traffic history: school start/dispersal, office peaks, hospital, main road")
    ax.legend(loc="upper center", bbox_to_anchor=(0.5, -0.12), ncol=4)
    plt.tight_layout()
    plt.savefig("history.png", dpi=130)
    print("history.png saved")


if __name__ == "__main__":
    main()
