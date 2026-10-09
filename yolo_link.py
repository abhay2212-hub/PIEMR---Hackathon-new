"""YOLO -> simulation link.  Your friend adds 3 lines to his own YOLO code.

    from yolo_link import CameraLink
    link = CameraLink()                       # same laptop: writes yolo_counts.json in this folder
    # link = CameraLink(server="http://192.168.1.5:8600")   # YOLO on ANOTHER laptop (same Wi-Fi)

    # inside his loop, for every frame:
    link.vehicle_passed("N", "car")           # when a tracked vehicle crosses the count line of road N
    link.queue("N", 6)                        # how many vehicles are waiting on road N right now
    link.tick()                               # call once per frame; sends the counts every 30 s

Roads are "N", "E", "S", "W" (the 4 roads of the junction in the video).
Vehicle names from any model are understood: car, motorcycle/motorbike/scooter/bicycle -> bike,
auto/autorickshaw/three-wheeler -> auto, bus, truck/lorry. Anything else counts as car.
"""
import json
import os
import time
import urllib.request

TYPE = {"car": "car", "van": "car", "taxi": "car", "jeep": "car",
        "motorcycle": "bike", "motorbike": "bike", "bike": "bike", "scooter": "bike", "bicycle": "bike",
        "auto": "auto", "autorickshaw": "auto", "auto-rickshaw": "auto", "auto_rickshaw": "auto",
        "rickshaw": "auto", "three-wheeler": "auto", "tempo": "auto",
        "bus": "bus", "minibus": "bus", "truck": "truck", "lorry": "truck", "tractor": "truck"}
ROADS = ("N", "E", "S", "W")


class CameraLink:
    def __init__(self, folder=None, server=None, window_s=30, roads=ROADS, key=None):
        self.folder = folder or os.path.dirname(os.path.abspath(__file__))
        self.server = server.rstrip("/") if server else None
        self.window = window_s
        self.key = key or os.environ.get("CONTROL_KEY", "")   # needed when the server has CONTROL_KEY set
        self.roads = roads
        self._reset()
        self.queues = {r: 0 for r in roads}
        self.sent = 0

    def _reset(self, now=None):
        self.start = now
        self.counts = {r: {"count": 0, "car": 0, "bike": 0, "auto": 0, "bus": 0, "truck": 0} for r in self.roads}

    def vehicle_passed(self, road, vehicle_class="car"):
        """A vehicle came from this road (crossed its count line). Count each vehicle once."""
        if road not in self.counts:
            return
        t = TYPE.get(str(vehicle_class).lower().strip(), "car")
        self.counts[road]["count"] += 1
        self.counts[road][t] += 1

    def queue(self, road, n):
        """Vehicles standing in the queue of this road right now."""
        if road in self.queues:
            self.queues[road] = int(n)

    def tick(self, now=None):
        """Call every frame. Sends the counts when the window (30 s) is over. Returns True when sent.
        now = video time in seconds (frame number / fps) for a video file; leave empty for a live camera."""
        now = time.time() if now is None else now
        if self.start is None:
            self.start = now
        if now - self.start < self.window:
            return False
        self.send(now - self.start, now)
        return True

    def close(self):
        """Call when the camera / video stops: SUMO then switches to normal traffic straight away."""
        data = {"time": time.time(), "ended": True, "roads": {}}
        try:
            if self.server:
                req = urllib.request.Request(self.server + "/api/yolo", data=json.dumps(data).encode(),
                                             headers={"Content-Type": "application/json", "X-Control-Key": self.key},
                                             method="POST")
                urllib.request.urlopen(req, timeout=3).read()
            else:
                path = os.path.join(self.folder, "yolo_counts.json")
                with open(path + ".tmp", "w") as f:
                    json.dump(data, f)
                os.replace(path + ".tmp", path)
            print("told the simulation: camera input ended")
        except Exception as e:
            print("could not tell the simulation:", e)

    def send(self, seconds=None, now=None):
        if seconds is None:
            seconds = (time.time() - self.start) if self.start is not None else self.window
        data = {"time": time.time(), "window_s": round(max(1.0, seconds), 1),
                "roads": {r: {**self.counts[r], "queue": self.queues[r]} for r in self.roads}}
        try:
            if self.server:
                req = urllib.request.Request(self.server + "/api/yolo", data=json.dumps(data).encode(),
                                             headers={"Content-Type": "application/json", "X-Control-Key": self.key},
                                             method="POST")
                urllib.request.urlopen(req, timeout=3).read()
            else:
                path = os.path.join(self.folder, "yolo_counts.json")
                with open(path + ".tmp", "w") as f:
                    json.dump(data, f)
                os.replace(path + ".tmp", path)
            self.sent += 1
            print("sent to simulation:", "  ".join(
                f"{r}: {d['count']} = {d['car']} cars, {d['bike']} bikes, {d['auto']} autos, {d['bus']} buses, "
                f"{d['truck']} trucks (queue {d['queue']})" for r, d in data["roads"].items()))
        except Exception as e:                       # never crash the YOLO program
            print("could not send counts:", e)
        self._reset(now)
        return data
