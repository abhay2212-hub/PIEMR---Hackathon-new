"""Pretend to be the YOLO camera: writes yolo_counts.json every 30 s with changing traffic.

    python fake_yolo.py            (leave it running in its own terminal)

Use it to test everything before the real YOLO is ready. Your teammate's YOLO must write
the SAME format:

{
  "time": 1696830000.0,          # when the counts were made (any number that changes)
  "window_s": 30,                # counts are for the last 30 seconds
  "roads": {
    "N": {"count": 12, "queue": 5, "car": 5, "bike": 4, "auto": 1, "bus": 1, "truck": 1},
    "E": {...}, "S": {...}, "W": {...}
  }
}
"""
import json
import os
import random
import time

WINDOW = 30                   # seconds between files (same as YOLO)
OUT = "yolo_counts.json"
BASE = {"N": 8, "E": 7, "S": 6, "W": 9}          # vehicles per 30 s on each road
SURGE_ROAD, SURGE_EXTRA = "E", 14                # every 10 min, road E gets busy for 4 min
MIX = {"car": 0.40, "bike": 0.35, "auto": 0.10, "bus": 0.05, "truck": 0.10}

os.chdir(os.path.dirname(os.path.abspath(__file__)))
rng = random.Random()
t0 = time.time()
print(f"Writing {OUT} every {WINDOW} s. Ctrl+C to stop.")
while True:
    minute = (time.time() - t0) / 60
    surge = (minute % 10) >= 2 and (minute % 10) < 6
    roads = {}
    for r, b in BASE.items():
        n = max(0, round(rng.gauss(b + (SURGE_EXTRA if surge and r == SURGE_ROAD else 0), 2)))
        types = {k: 0 for k in MIX}
        for _ in range(n):
            types[rng.choices(list(MIX), list(MIX.values()))[0]] += 1
        roads[r] = {"count": n, "queue": max(0, round(n * rng.uniform(0.3, 0.7))), **types}
    data = {"time": time.time(), "window_s": WINDOW, "roads": roads}
    with open(OUT + ".tmp", "w") as f:
        json.dump(data, f, indent=1)
    os.replace(OUT + ".tmp", OUT)
    print(time.strftime("%H:%M:%S"), ("SURGE on " + SURGE_ROAD + " | ") if surge else "",
          "  ".join(f"{r}:{m['count']}(q{m['queue']})" for r, m in roads.items()))
    time.sleep(WINDOW)
