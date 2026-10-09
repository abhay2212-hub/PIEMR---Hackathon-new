"""Ready-made camera counter: YOLO + tracking -> counts per road -> simulation.

Use this if your friend's YOLO only DETECTS vehicles and does not count them per road.
(If his code already counts per road, he only needs yolo_link.py - see YOLO_LINK.md.)

    pip install ultralytics opencv-python lap

    # 1) once: draw the 4 roads on the first frame of the video
    python camera_yolo.py --source junction.mp4 --draw
    # 2) run: counts every 30 s go to the simulation
    python camera_yolo.py --source junction.mp4
    python camera_yolo.py --source junction.mp4 --model friend_best.pt      # your friend's trained model
    python camera_yolo.py --source http://192.168.1.23:4747/video           # phone camera (DroidCam / IP Webcam)
    python camera_yolo.py --source 0 --server http://192.168.1.5:8600       # webcam, simulation on another laptop

Drawing (window "draw roads"):
    1 2 3 4   choose road N / E / S / W
    click     points of the WAITING AREA of that road (where the queue stands), 3 or more points
    l         then click 2 points = the COUNT LINE (stop line) of that road
    p         back to waiting-area clicking      u  undo      c  clear this road
    s         save                               Esc  quit without saving
"""
import argparse
import json
import os
import time

import cv2
import numpy as np

from yolo_link import TYPE, CameraLink

HERE = os.path.dirname(os.path.abspath(__file__))
COL = {"N": (255, 128, 0), "E": (0, 200, 255), "S": (0, 220, 0), "W": (220, 0, 220)}
KEYS = {"1": "N", "2": "E", "3": "S", "4": "W"}


def open_video(src):
    cap = cv2.VideoCapture(int(src) if str(src).isdigit() else src)
    if not cap.isOpened():
        raise SystemExit(f"Cannot open video: {src}")
    return cap


def draw(src, path):
    cap = open_video(src)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise SystemExit("Cannot read a frame")
    h, w = frame.shape[:2]
    z = {r: {"area": [], "line": []} for r in "NESW"}
    cur, mode = "N", "area"

    def click(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN:
            if mode == "line" and len(z[cur]["line"]) >= 2:
                z[cur]["line"] = []
            z[cur][mode].append([x / w, y / h])

    cv2.namedWindow("draw roads")
    cv2.setMouseCallback("draw roads", click)
    while True:
        v = frame.copy()
        for r, d in z.items():
            pts = np.array([[x * w, y * h] for x, y in d["area"]], np.int32)
            for p in pts:
                cv2.circle(v, tuple(p), 4, COL[r], -1)
            if len(pts) >= 3:
                cv2.polylines(v, [pts], True, COL[r], 2)
                cx, cy = pts.mean(axis=0).astype(int)
                cv2.putText(v, r, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 1, COL[r], 3)
            if len(d["line"]) == 2:
                a, b = [(int(x * w), int(y * h)) for x, y in d["line"]]
                cv2.line(v, a, b, (0, 0, 255), 3)
        cv2.rectangle(v, (0, 0), (w, 30), (0, 0, 0), -1)
        cv2.putText(v, f"road {cur} | {'click waiting area' if mode == 'area' else 'click 2 points of count line'}"
                       " | 1-4 road  l line  p area  u undo  s save", (8, 21), cv2.FONT_HERSHEY_SIMPLEX, .55, (255, 255, 255), 1)
        cv2.imshow("draw roads", v)
        k = cv2.waitKey(30) & 0xFF
        ch = chr(k) if k < 128 else ""
        if ch in KEYS:
            cur, mode = KEYS[ch], "area"
        elif ch == "l":
            mode = "line"
        elif ch == "p":
            mode = "area"
        elif ch == "u" and z[cur][mode]:
            z[cur][mode].pop()
        elif ch == "c":
            z[cur] = {"area": [], "line": []}
        elif ch == "s":
            keep = {r: d for r, d in z.items() if len(d["area"]) >= 3 and len(d["line"]) == 2}
            json.dump(keep, open(path, "w"), indent=1)
            print(f"Saved roads {list(keep)} to {path}")
            break
        elif k == 27:
            break
    cv2.destroyAllWindows()


def crossed(p, q, a, b):
    s = lambda u, v, w_: np.sign((v[0] - u[0]) * (w_[1] - u[1]) - (v[1] - u[1]) * (w_[0] - u[0]))  # noqa: E731
    return s(a, b, p) != s(a, b, q) and s(p, q, a) != s(p, q, b)


def run(a):
    if not os.path.exists(a.zones):
        raise SystemExit(f"No {a.zones}. First run:  python camera_yolo.py --source {a.source} --draw")
    try:
        from ultralytics import YOLO
    except ImportError:
        raise SystemExit("pip install ultralytics opencv-python lap")
    zones = json.load(open(a.zones))
    model = YOLO(a.model)
    names = model.names
    keep = [i for i, n in names.items() if str(n).lower() in TYPE]
    cap = open_video(a.source)
    ok, frame = cap.read()
    h, w = frame.shape[:2]
    Z = {r: {"area": np.array([[x * w, y * h] for x, y in d["area"]], np.int32),
             "line": [(x * w, y * h) for x, y in d["line"]]} for r, d in zones.items()}
    link = CameraLink(server=a.server, window_s=a.window, roads=tuple(Z))
    last, counted, moved = {}, set(), {}
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    publish_every = max(1, round(fps / 2))
    is_file = os.path.exists(str(a.source))
    frame_no, loop_offset, t0 = 0, 0.0, time.time()
    print(f"Counting roads {list(Z)} with {a.model}. Counts go to the simulation every {a.window} s. q = stop")
    while ok:
        res = model.track(frame, persist=True, tracker="bytetrack.yaml", classes=keep or None,
                          conf=a.conf, verbose=False)[0]
        queue = {r: 0 for r in Z}
        if res.boxes is not None and res.boxes.id is not None:
            for (x1, y1, x2, y2), tid, c in zip(res.boxes.xyxy.tolist(), res.boxes.id.int().tolist(),
                                                res.boxes.cls.int().tolist()):
                name = str(names[c]).lower()
                p = ((x1 + x2) / 2, y2)                         # where the wheels touch the road
                prev = last.get(tid)
                speed = np.hypot(p[0] - prev[0], p[1] - prev[1]) * fps if prev else 0
                moved[tid] = 0.7 * moved.get(tid, speed) + 0.3 * speed
                for r, z in Z.items():
                    if prev and (tid, r) not in counted and crossed(prev, p, *z["line"]):
                        counted.add((tid, r))
                        link.vehicle_passed(r, name)
                    if cv2.pointPolygonTest(z["area"], p, False) >= 0 and moved[tid] < a.still_px:
                        queue[r] += 1
                last[tid] = p
                if a.show:
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (255, 200, 0), 2)
                    cv2.putText(frame, name, (int(x1), int(y1) - 4), cv2.FONT_HERSHEY_SIMPLEX, .5, (255, 200, 0), 1)
        for r, n in queue.items():
            link.queue(r, n)
        frame_no += 1
        if is_file and a.realtime:                          # play the file at normal speed (same pace as SUMO)
            ahead = t0 + loop_offset + frame_no / fps - time.time()
            if ahead > 0:
                time.sleep(ahead)
        link.tick(loop_offset + frame_no / fps if is_file else None)    # video time for a file, clock for a camera
        if a.show:
            for r, z in Z.items():
                cv2.polylines(frame, [z["area"]], True, COL[r], 2)
                (ax, ay), (bx, by) = z["line"]
                cv2.line(frame, (int(ax), int(ay)), (int(bx), int(by)), (0, 0, 255), 2)
                cx, cy = z["area"].mean(axis=0).astype(int)
                cv2.putText(frame, f"{r}: {link.counts[r]['count']} passed, {queue[r]} waiting", (cx - 60, cy),
                            cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2)
            cv2.imshow("camera", frame)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
        if frame_no % publish_every == 0:
            encoded, jpeg = cv2.imencode(".jpg", frame)
            if encoded:
                link.publish_frame(jpeg.tobytes())
        ok, frame = cap.read()
        if not ok and a.loop and not str(a.source).isdigit() and "://" not in str(a.source):
            cap.release()
            cap = open_video(a.source)                      # play the video again (long demos)
            ok, frame = cap.read()
            last.clear()
            loop_offset += frame_no / fps
            frame_no = 0
    cap.release()
    cv2.destroyAllWindows()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="video file, webcam number (0) or phone stream URL")
    ap.add_argument("--draw", action="store_true", help="draw the 4 roads on the first frame")
    ap.add_argument("--zones", default=os.path.join(HERE, "camera_roads.json"))
    ap.add_argument("--model", default="yolov8n.pt", help="your friend's .pt file, or yolov8n.pt")
    ap.add_argument("--conf", type=float, default=0.3)
    ap.add_argument("--window", type=int, default=30, help="seconds between updates to the simulation")
    ap.add_argument("--still-px", type=float, default=15, help="pixels/second below which a vehicle counts as waiting")
    ap.add_argument("--server", default=None, help="http://<simulation-laptop-IP>:8600 if YOLO runs on another laptop")
    ap.add_argument("--no-show", dest="show", action="store_false")
    ap.add_argument("--no-loop", dest="loop", action="store_false")
    ap.add_argument("--fast", dest="realtime", action="store_false", help="do not slow a video file down to real time")
    a = ap.parse_args()
    draw(a.source, a.zones) if a.draw else run(a)
