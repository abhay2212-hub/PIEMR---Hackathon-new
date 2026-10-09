"""Your teammate's main.py (phone CCTV + YOLO) connected to the simulation.

Same live detection window and TRAFFIC ALERT as his version, plus:
  - tracking, so each vehicle is counted once
  - every WINDOW seconds the counts go to the simulation (yolo_counts.json)

    python main_linked.py --source 192.168.1.7              # PHONE camera: the IP shown in the DroidCam app
    python main_linked.py                                   # phone stream at PHONE_STREAM below
    python main_linked.py --source video.mp4                # a video file instead (loops)
    python main_linked.py --source usb                      # PHONE by USB (DroidCam PC client connected)
    python main_linked.py --source 1                        # a camera by number (0 = laptop webcam, NOT the phone)

The camera watches ONE road of the junction. Which SUMO road that is: settings.py ->
    CAMERA_JUNCTION = "J1"
    CAMERA_ROADS = {"W": "E0"}
"""
import argparse
import json
import os
import time

import cv2
from ultralytics import YOLO

from yolo_link import TYPE, CameraLink

FRAME_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_frame.jpg")
STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "camera_stats.json")


def save_atomic(path, data):
    """Write a file in one go. On Windows the web server may be reading it: then skip this frame."""
    try:
        with open(path + ".tmp", "wb") as f:
            f.write(data)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


class WebView:
    """Publishes the annotated YOLO picture + counts for the authority web page (camera_web.py)."""

    def __init__(self, source, road):
        self.source, self.road = str(source), road
        self.t_frame = self.t_stats = 0.0
        self.times = []

    def frame(self, img):
        now = time.time()
        self.times = [t for t in self.times if now - t < 2] + [now]
        if now - self.t_frame < 0.1:                     # max 10 pictures per second
            return
        self.t_frame = now
        h, w = img.shape[:2]
        if w > 960:
            img = cv2.resize(img, (960, int(h * 960 / w)))
        ok, jpg = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if ok:
            save_atomic(FRAME_FILE, jpg.tobytes())

    def stats(self, now_counts, total, waiting, alert, latency, window_count, last_sent, live=True):
        now = time.time()
        if live and now - self.t_stats < 0.25:
            return
        self.t_stats = now
        data = {"time": now, "live": live, "source": self.source, "road": self.road,
                "in_view": now_counts, "counted": total, "waiting": waiting, "alert": alert,
                "latency_ms": round(latency), "fps": round(len(self.times) / 2, 1),
                "window_count": window_count, "last_sent": last_sent}
        save_atomic(STATS_FILE, json.dumps(data).encode())

PHONE_STREAM = "http://172.10.23.162:4747/video"     # DroidCam phone address (change to yours)
ROAD = "W"                                            # which road of the junction the camera watches
VEHICLE_CLASSES = [1, 2, 3, 5, 7]                     # COCO: bicycle, car, motorcycle, bus, truck
KINDS = ("car", "bike", "auto", "bus", "truck")
COLORS = {"car": (255, 200, 0), "bike": (0, 255, 255), "auto": (0, 165, 255), "bus": (255, 0, 255), "truck": (80, 80, 255)}
WIN = "Phone CCTV - Live YOLO Detection"
ALERT_COUNT = 3                                       # more vehicles than this in view = TRAFFIC ALERT
HERE = os.path.dirname(os.path.abspath(__file__))


def phone_url(src):
    """'192.168.1.7' or '192.168.1.7:4747'  ->  'http://192.168.1.7:4747/video' (DroidCam phone address)."""
    s = str(src).strip()
    host = s.split(":")[0]
    if host.count(".") == 3 and host.replace(".", "").isdigit():
        return f"http://{s if ':' in s else s + ':4747'}/video"
    return s


def open_webcam(i):
    """Webcam number i. On Windows DirectShow is used: it sees the DroidCam USB camera."""
    cap = cv2.VideoCapture(i, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(i)
    if not cap.isOpened():
        cap = cv2.VideoCapture(i)
    return cap


def find_usb_phone():
    """DroidCam over USB = an extra webcam. 0 is the laptop's own camera, so try 1, 2, 3... first."""
    for i in (1, 2, 3, 4, 5, 0):
        cap = open_webcam(i)
        ok, frame = cap.read() if cap.isOpened() else (False, None)
        if ok and frame is not None:
            print(f"Using camera number {i}. If this is NOT the phone, run with --source {i + 1}", flush=True)
            cap.release()
            return i
        cap.release()
    raise SystemExit("No phone camera found. Open the DroidCam PC client, connect by USB and press Start, then try again.")


def open_source(src):
    if str(src).lower() == "usb":
        src = find_usb_phone()
    src = phone_url(src)
    cap = open_webcam(int(src)) if str(src).isdigit() else cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit(f"Could not open {src}.\n"
                         "  1. Open the DroidCam app on the phone and keep it open (screen on).\n"
                         "  2. Phone and laptop on the SAME Wi-Fi / hotspot.\n"
                         "  3. Use the IP shown in the app:  python main_linked.py --source 192.168.x.x\n"
                         "  4. Close the DroidCam PC client and any browser tab showing the camera (only one viewer allowed).")
    print("Camera:", src, flush=True)
    return cap


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default=PHONE_STREAM)
    ap.add_argument("--model", default=os.path.join(HERE, "yolo11n.pt"))
    ap.add_argument("--road", default=ROAD)
    ap.add_argument("--window", type=int, default=10, help="seconds between updates to the simulation")
    ap.add_argument("--server", default=None, help="http://<simulation-laptop-IP>:8600 (or cloud server) if SUMO runs on another laptop")
    ap.add_argument("--key", default=None, help="CONTROL_KEY of the server (cloud)")
    ap.add_argument("--conf", type=float, default=0.20, help="lower = finds more (bikes), higher = fewer false boxes")
    ap.add_argument("--size", type=int, default=640, help="960 or 1280 finds small/far bikes better (slower)")
    ap.add_argument("--loop", action="store_true", help="repeat a video file (default: play once, then normal traffic)")
    ap.add_argument("--web-port", type=int, default=0, help="extra camera page without login, e.g. 8601 (default off)")
    ap.add_argument("--no-web", action="store_true", help="do not show the camera on the authority web page")
    ap.add_argument("--hide-window", action="store_true", help="no OpenCV window, only the web page (stop: Ctrl+C)")
    ap.add_argument("--all", action="store_true", help="show EVERY object YOLO sees (to check what it thinks a bike is)")
    a = ap.parse_args()

    print("Loading YOLO model...", flush=True)
    model = YOLO(a.model)
    names = model.names
    cap = open_source(a.source)
    is_file = os.path.exists(str(a.source))
    fps = cap.get(cv2.CAP_PROP_FPS) or 25
    link = CameraLink(server=a.server, key=a.key, window_s=a.window, roads=(a.road,))
    seen, last_pos, frame_no, t0 = set(), {}, 0, time.time()
    total = {k: 0 for k in KINDS}                              # counted since start, per type
    web = None if a.no_web else WebView(a.source, a.road)
    if web and a.web_port:
        import camera_web
        camera_web.serve(a.web_port)                              # live camera page on port 8601
    last_sent, now, waiting, latency = None, {k: 0 for k in KINDS}, 0, 0
    print(f"Connected. Counting road {a.road}, sending to the simulation every {a.window} s. Q = stop", flush=True)

    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                if is_file and a.loop:                        # repeat a video file
                    cap.release()
                    cap = open_source(a.source)
                    seen.clear()
                    continue
                print("Video finished." if is_file else "No frame received. Check the phone stream.")
                break
            frame_no += 1
            start = time.perf_counter()
            result = model.track(frame, persist=True, tracker="bytetrack.yaml", imgsz=a.size, conf=a.conf,
                                 classes=None if a.all else VEHICLE_CLASSES, verbose=False)[0]
            latency = (time.perf_counter() - start) * 1000

            now = {k: 0 for k in KINDS}                        # in view right now, per type
            waiting = 0
            boxes = result.boxes
            if boxes is not None and boxes.id is not None:
                for (x1, y1, x2, y2), tid, c in zip(boxes.xyxy.tolist(), boxes.id.int().tolist(), boxes.cls.int().tolist()):
                    kind = TYPE.get(str(names[c]).lower())
                    if kind is None:                          # person, dog... (only shown with --all)
                        continue
                    now[kind] += 1
                    if tid not in seen:                       # a new vehicle on this road
                        seen.add(tid)
                        total[kind] += 1
                        link.vehicle_passed(a.road, names[c])
                    p = ((x1 + x2) / 2, y2)
                    q = last_pos.get(tid)
                    if q and abs(p[0] - q[0]) + abs(p[1] - q[1]) < 2:   # hardly moved since last frame = waiting
                        waiting += 1
                    last_pos[tid] = p
            in_view = sum(now.values())
            link.queue(a.road, waiting)
            sent = link.tick(frame_no / fps if is_file else None)
            if sent:
                last_sent = time.time()

            if is_file:                                       # play a file at normal speed (same pace as SUMO)
                ahead = t0 + frame_no / fps - time.time()
                if ahead > 0:
                    time.sleep(ahead)

            display = result.plot()
            clean = display.copy()                            # web page: boxes only, numbers go to the tiles
            alert = in_view > ALERT_COUNT
            F, W = cv2.FONT_HERSHEY_SIMPLEX, (255, 255, 255)
            cv2.rectangle(display, (10, 10), (560, 230), (25, 25, 25), -1)
            cv2.putText(display, "TRAFFIC ALERT" if alert else "NO TRAFFIC ALERT", (20, 40), F, 0.75,
                        (0, 0, 255) if alert else (0, 200, 0), 2)
            cv2.putText(display, f"{'':8}{'Cars':>6}{'Bikes':>7}{'Autos':>7}{'Buses':>7}{'Trucks':>8}", (20, 72), F, 0.55, (200, 200, 200), 1)
            for row, (label, d) in enumerate((("In view", now), ("Counted", total))):
                y = 102 + row * 32
                cv2.putText(display, label, (20, y), F, 0.55, W, 1)
                for col, k in enumerate(KINDS):
                    cv2.putText(display, str(d[k]), (120 + col * 85 + (10 if k == "truck" else 0), y), F, 0.7, COLORS[k], 2)
            cv2.putText(display, f"Total in view: {in_view}   waiting: {waiting}   counted: {sum(total.values())}",
                        (20, 172), F, 0.55, W, 1)
            cv2.putText(display, f"Road {a.road}: {link.counts[a.road]['count']} this window" + ("  -> SENT to SUMO" if sent else ""),
                        (20, 198), F, 0.5, (255, 255, 0), 1)
            cv2.putText(display, f"Inference {latency:.0f} ms" + ("   [--all: showing every object]" if a.all else ""),
                        (20, 220), F, 0.45, W, 1)
            if web:                                           # same picture on the authority web page
                web.frame(clean)
                web.stats(now, total, waiting, alert, latency, link.counts[a.road]["count"], last_sent)
            if a.hide_window:
                continue
            cv2.imshow(WIN, display)
            if cv2.waitKey(1) & 0xFF == ord("q"):
                break
            if cv2.getWindowProperty(WIN, cv2.WND_PROP_VISIBLE) < 1:     # window closed with X
                break
    finally:
        if web:
            web.stats(now, total, waiting, False, latency, 0, last_sent, live=False)
        link.close()                                          # SUMO: camera input over -> normal traffic
        cap.release()
        cv2.destroyAllWindows()
        print("Detection stopped.", flush=True)


if __name__ == "__main__":
    main()
