"""Compare our system with the baseline (fixed signals).

    python compare.py      -> table in the terminal + comparison.png (charts for the judges)
"""
import csv
import xml.etree.ElementTree as ET

TYPES = ("car", "bike", "auto", "bus", "truck")


def parse(path):
    """Read tripinfo; also works if the run was stopped early (file not closed)."""
    try:
        return ET.parse(path).getroot()
    except ET.ParseError:
        with open(path) as f:
            text = f.read()
        end = text.rfind("</tripinfo>")
        text = text[:end + 11] if end >= 0 else text[:text.rfind("/>") + 2]
        body = text[text.find("<tripinfo "):] if "<tripinfo " in text else ""
        return ET.fromstring("<tripinfos>" + body + "</tripinfos>")
    except FileNotFoundError:
        raise SystemExit(f"{path} not found - run city.py fixed and city.py smart (or both.py) first")


def trips(path):
    out = []
    for t in parse(path).iter("tripinfo"):
        if t.get("vaporized"):                  # removed background vehicles (camera roads) - not real trips
            continue
        em = t.find("emissions")
        out.append({"type": t.get("vType"), "delay": float(t.get("timeLoss")),
                    "wait": float(t.get("waitingTime")), "travel": float(t.get("duration")),
                    "stops": float(t.get("waitingCount", 0)),
                    "co2": float(em.get("CO2_abs")) / 1000 if em is not None else 0,       # g
                    "fuel": float(em.get("fuel_abs")) / 1000 if em is not None else 0})    # ml (SUMO >=1.14: mg)
    return out


def avg(rows, key, vtype=None):
    r = [x[key] for x in rows if vtype is None or x["type"] == vtype]
    return sum(r) / len(r) if r else 0


def timeline(path):
    try:
        with open(path) as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


F, S = trips("tripinfo_fixed.xml"), trips("tripinfo_smart.xml")
rows = [("Vehicles finished", len(F), len(S), True),
        ("Avg delay (s)", avg(F, "delay"), avg(S, "delay"), False),
        ("Avg waiting at red (s)", avg(F, "wait"), avg(S, "wait"), False),
        ("Avg travel time (s)", avg(F, "travel"), avg(S, "travel"), False),
        ("Avg stops per vehicle", avg(F, "stops"), avg(S, "stops"), False),
        ("Total CO2 (kg)", sum(x["co2"] for x in F) / 1000, sum(x["co2"] for x in S) / 1000, False)]

print(f"\n{'':26s}{'BASELINE':>10s}{'OUR SYSTEM':>12s}{'CHANGE':>10s}")
for name, f, s, higher_better in rows:
    ch = f"{100 * (s - f) / f:+.1f}%" if f else ""
    print(f"{name:26s}{f:10.1f}{s:12.1f}{ch:>10s}")
print("\nAvg delay by vehicle type:")
for vt in TYPES:
    f, s = avg(F, "delay", vt), avg(S, "delay", vt)
    if f:
        print(f"  {vt:6s} baseline {f:6.1f}s  our system {s:6.1f}s  ({100 * (s - f) / f:+.1f}%)")

# ---------------- charts ----------------
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
except ImportError:
    raise SystemExit("\nFor the charts: pip install matplotlib   (then run compare.py again)")

GREY, GREEN = "#9aa0a6", "#1e8e3e"
fig, ax = plt.subplots(2, 2, figsize=(14, 9))
fig.suptitle("Our system vs baseline (fixed-time signals) - same traffic", fontsize=16, weight="bold")

# 1) headline bars
a = ax[0, 0]
names = ["Avg delay", "Avg waiting", "Avg travel time"]
fv = [avg(F, "delay"), avg(F, "wait"), avg(F, "travel")]
sv = [avg(S, "delay"), avg(S, "wait"), avg(S, "travel")]
x = range(len(names))
a.bar([i - 0.2 for i in x], fv, 0.4, label="Baseline", color=GREY)
a.bar([i + 0.2 for i in x], sv, 0.4, label="Our system", color=GREEN)
for i in x:
    if fv[i]:
        a.text(i + 0.2, sv[i], f"{100 * (sv[i] - fv[i]) / fv[i]:+.0f}%", ha="center", va="bottom",
               weight="bold", color=GREEN)
a.set_xticks(list(x), names)
a.set_ylabel("seconds per vehicle")
a.set_title("Time lost per vehicle")
a.set_ylim(0, a.get_ylim()[1] * 1.15)
a.legend(loc="upper left", ncol=2)

# 2) waiting vehicles over time
a = ax[0, 1]
tf, ts = timeline("timeline_fixed.csv"), timeline("timeline_smart.csv")
if tf and ts:
    a.plot([int(r["time_s"]) / 60 for r in tf], [int(r["waiting"]) for r in tf], color=GREY, label="Baseline")
    a.plot([int(r["time_s"]) / 60 for r in ts], [int(r["waiting"]) for r in ts], color=GREEN, label="Our system")
    a.set_xlabel("minutes")
    a.legend()
else:
    a.text(0.5, 0.5, "run city.py fixed and smart\nto get timeline_*.csv", ha="center", transform=a.transAxes)
a.set_ylabel("vehicles waiting in the network")
a.set_title("Queues over time")

# 3) by vehicle type
a = ax[1, 0]
vts = [vt for vt in TYPES if avg(F, "delay", vt)]
x = range(len(vts))
a.bar([i - 0.2 for i in x], [avg(F, "delay", v) for v in vts], 0.4, label="Baseline", color=GREY)
a.bar([i + 0.2 for i in x], [avg(S, "delay", v) for v in vts], 0.4, label="Our system", color=GREEN)
a.set_xticks(list(x), vts)
a.set_ylabel("avg delay (s)")
a.set_title("Delay by vehicle type")
a.set_ylim(0, a.get_ylim()[1] * 1.2)
a.legend(loc="upper center", ncol=2)

# 4) summary card
a = ax[1, 1]
a.axis("off")
lines = []
for name, f, s, higher_better in rows:
    ch = f"{100 * (s - f) / f:+.1f}%" if f else ""
    lines.append(f"{name:24s} {f:9.1f} {s:11.1f} {ch:>8s}")
a.text(0, 1, f"{'':24s} {'Baseline':>9s} {'Our system':>11s} {'Change':>8s}\n" + "\n".join(lines),
       family="monospace", fontsize=11, va="top")
a.set_title("Summary")

plt.tight_layout()
plt.savefig("comparison.png", dpi=150)
print("\nCharts saved: comparison.png")
