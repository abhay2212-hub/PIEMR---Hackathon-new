"""Run BASELINE (left) and OUR SYSTEM (right) at the same time with the same traffic.

    python both.py                      normal traffic
    python both.py --yolo               traffic from the camera (run YOLO or fake_yolo.py too)
    python both.py --yolo --predict     + prediction (run history.py and predict.py once first)
    python both.py --yolo --predict --nogui   on a cloud server (no windows)
    python both.py --predict --day Sat  use the predicted rush times of a Saturday (Mon..Sun)

The terminal shows OUR SYSTEM; the baseline's messages go to log_fixed.txt.
When both finish (or you press Ctrl+C), compare.py makes comparison.png.
"""
import os
import subprocess
import sys
import time

import settings as S

# ===== SETTINGS =====
SCREEN_W, SCREEN_H = 1920, 1040      # your screen (e.g. 1366, 728 for a small laptop)
WAIT_TO_START = 12                   # seconds for both windows to open
# ====================

os.chdir(S.HERE)
flags = [a for a in sys.argv[1:] if a in ("--yolo", "--predict", "--nogui")]
if "--day" in sys.argv[1:-1]:                  # e.g. --day Sat : simulate that day's predicted rushes
    flags += ["--day", sys.argv[sys.argv.index("--day") + 1]]
S.write_json(S.DEMO_FILE, {})                  # start un-paused
for f in ("progress_smart.json",):
    if os.path.exists(f):
        os.remove(f)
half = SCREEN_W // 2
start_at = str(time.time() + WAIT_TO_START)
size = f"{half - 10},{SCREEN_H}"

with open("log_fixed.txt", "w") as log:
    fixed = subprocess.Popen([sys.executable, "city.py", "fixed", *flags, "--pos", "0,0", "--size", size,
                              "--start-at", start_at], stdout=log, stderr=subprocess.STDOUT)
    smart = subprocess.Popen([sys.executable, "city.py", "smart", *flags, "--pos", f"{half},0", "--size", size,
                              "--start-at", start_at])
    print(f"LEFT = BASELINE   |   RIGHT = OUR SYSTEM   (starting in {WAIT_TO_START} s)")
    print("Control the run from the dashboard: http://localhost:8600 (start dashboard_server.py)")
    try:
        smart.wait()
        fixed.wait()
    except KeyboardInterrupt:
        for p in (fixed, smart):
            p.terminate()
        time.sleep(2)

print("\nComparing...")
subprocess.run([sys.executable, "compare.py"])
