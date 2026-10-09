"""ALL SETTINGS in one place + helpers to read your network (no SUMO needed for these).

Change things here, not in the other files.
"""
import json
import math
import os
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))

# ======================= FILES =======================
NET = "net.net.xml"              # your network from netedit
ROUTES = "traffic.rou.xml"       # made by make_traffic.py
VTYPES = "vtypes.xml"            # vehicle shapes, made by make_traffic.py

# ======================= TIME ========================
START_CLOCK = "07:15"            # clock time when the simulation starts
SIM_TIME = 3600                  # seconds to simulate
DEMO_DAY = "Mon"                 # day used for the prediction (Mon..Sun)
DELAY = 150                      # ms per simulated second in sumo-gui (0 = fastest)

# ======================= SIGNALS =====================
THRESHOLD = 6                    # more than 6 (weighted) waiting -> that road gets green
LOW_TRAFFIC = 3                  # 3 or fewer vehicles on the green road -> red early
EARLY_GREEN = 5                  # ...but at least 5 s of green first
MIN_GREEN = 10
MAX_GREEN = 60
YELLOW = 3
JAM_LIMIT = 15                   # waiting vehicles on one road = JAM

# ============ LEAST-CONGESTION RECOMMENDATION ========
CONGESTED = 8                    # waiting vehicles -> look for a better route
MIN_SAVING = 15                  # recommend only if at least 15 s faster
DIVERT_SHARE = 0.5               # share of affected vehicles that follow the advice
PRE_DIVERT_SHARE = 0.7           # share of drivers that follow the PREDICTED diversion (app / signboard)
PRE_DIVERT_COLOR = (255, 140, 0, 255)   # pre-diverted vehicles turn ORANGE
CHECK_EVERY = 30                 # seconds between checks
APPROVAL = "auto"                # "auto" = act at once, "manual" = operator approves in dashboard

# ======================= PLACES ======================
# "edge": road id from netedit (e.g. "E5"); None = picked automatically.
# "times": when the place needs priority. With --predict the system also
#          prepares PREPARE_MIN minutes before the PREDICTED rush.
PLACES = [
    {"name": "School", "edge": None, "times": [("07:30", "08:30"), ("13:30", "14:30")],
     "weight": 2.0, "speed_kmh": 25, "color": (255, 200, 0, 255), "img": "school.png"},
    {"name": "Office", "edge": None, "times": [("09:00", "10:00"), ("18:00", "19:00")],
     "weight": 1.5, "color": (60, 120, 255, 255), "img": "office.png"},
    {"name": "Hospital", "edge": None, "times": [("00:00", "23:59")],
     "weight": 1.5, "color": (230, 40, 40, 255), "img": "hospital.png"},
]
PREPARE_MIN = 10                 # minutes before a predicted rush to start preparing
PLACE_SURGE = 500                # veh/h extra to/from a place during its rush (make_traffic.py)

# ======================= CAMERA / YOLO ===============
YOLO_FILE = "yolo_counts.json"   # written by YOLO (or fake_yolo.py) every 30 s
CAMERA_JUNCTION = None           # junction id the video shows (e.g. "J2"); None = automatic
CAMERA_ROADS = None              # e.g. {"N": "-E3", "E": "-E4", "S": "E7", "W": "E1"}; None = automatic
YOLO_EVERY = 10                  # seconds between reading the YOLO file
YOLO_SCALE = 1.0                 # 2.0 = double the counted traffic
YOLO_DELAY = 1000                # ms per simulated second with --yolo (1000 = real time, same as video)
CAMERA_COLOR = (255, 0, 255, 255)    # vehicles that came from the YOLO camera are MAGENTA in SUMO
YOLO_BACKGROUND = "after"        # "after" = ONLY camera vehicles while YOLO runs, normal traffic starts when YOLO stops
                                 # False = only camera vehicles, always     True = camera + normal traffic together
YOLO_TIMEOUT = 25                # seconds without new counts = YOLO has stopped
YOLO_FIRST = 20                  # "after": seconds of camera-only traffic, then normal traffic joins in
YOLO_WAIT = 60                   # "after": if YOLO never starts, normal traffic starts after this many seconds

# ======================= DASHBOARD ===================
LIVE_EVERY = 2                   # seconds between dashboard updates
COMMANDS = "commands.json"       # operator dashboard -> simulation

# ======================= DEMO (show the stages slowly) ==============
CONTROL_KEY = ""                 # set e.g. "abc123" when the dashboard is online: buttons then need ?key=abc123
AUTH_USERS = {}                  # userid: pbkdf2$iterations$salt_hex$hash_hex (generate with make_user.py)
SESSION_HOURS = 8
DEMO_FILE = "demo_control.json"  # pause / speed / jump - shared by both windows
AUTO_PAUSE = ["PREDICTED", "PRE-DIVERT", "PRIORITY", "RECOMMEND", "DIVERT", "JAM"]   # pause BOTH windows at these stages
AUTO_PAUSE_ONCE = True           # pause only the first time each stage happens ([] above = never pause)
SLOW_DELAY = 600                 # ms per simulated second right after a stage (slow motion), 0 = off
SLOW_FOR = 25                    # simulated seconds of slow motion after a stage
FOCUS_ON_STAGE = True            # zoom both windows to the road where it happens
FOCUS_SIZE = 150                 # metres shown around that road when zoomed
# =====================================================


# ---------------------------------------------------------------- helpers
def hhmm(s):
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60


def clock(t):
    c = int(hhmm(START_CLOCK) + t) % 86400
    return f"{c // 3600:02d}:{(c % 3600) // 60:02d}"


def sec_of_day(t):
    return (hhmm(START_CLOCK) + t) % 86400


def in_windows(now, windows, before=0):
    return any(hhmm(a) - before <= now <= hhmm(b) for a, b in windows)


def read_json(path, default=None):
    try:
        with open(os.path.join(HERE, path)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path, data):
    """Write safely (the dashboard never sees a half-written file)."""
    full = os.path.join(HERE, path)
    tmp = f"{full}.{os.getpid()}.tmp"     # own temp file per program (two windows write at once)
    with open(tmp, "w") as f:
        json.dump(data, f)
    for _ in range(5):
        try:
            os.replace(tmp, full)
            return
        except PermissionError:       # Windows: file open in another program, try again
            import time
            time.sleep(0.05)


# ---------------------------------------------------------------- network
class Net:
    """Roads, junctions and connections read straight from net.net.xml."""

    def __init__(self, path=NET):
        root = ET.parse(os.path.join(HERE, path)).getroot()
        self.edges = {}
        for e in root.iter("edge"):
            if e.get("function") == "internal":
                continue
            lanes = e.findall("lane")
            if not lanes:
                continue
            shape = [tuple(map(float, p.split(","))) for p in lanes[0].get("shape").split()]
            self.edges[e.get("id")] = {
                "from": e.get("from"), "to": e.get("to"), "lanes": len(lanes),
                "length": float(lanes[0].get("length")), "speed": float(lanes[0].get("speed")),
                "shape": shape,
                "shapes": [[tuple(map(float, p.split(","))) for p in ln.get("shape").split()] for ln in lanes]}
        self.junctions = {}
        for j in root.iter("junction"):
            if j.get("type") == "internal":
                continue
            self.junctions[j.get("id")] = {"type": j.get("type"), "x": float(j.get("x")), "y": float(j.get("y"))}
        self.next = {e: set() for e in self.edges}          # which road can follow which
        for c in root.iter("connection"):
            a, b = c.get("from"), c.get("to")
            if a in self.next and b in self.edges:
                self.next[a].add(b)
        neigh = {}
        for e in self.edges.values():
            neigh.setdefault(e["from"], set()).add(e["to"])
            neigh.setdefault(e["to"], set()).add(e["from"])
        self.fringe = {n for n, s in neigh.items() if len(s) == 1}
        self.entries = sorted(e for e, d in self.edges.items() if d["from"] in self.fringe)
        self.exits = sorted(e for e, d in self.edges.items() if d["to"] in self.fringe)
        self.tls = sorted(j for j, d in self.junctions.items() if "traffic_light" in (d["type"] or ""))
        xs = [x for e in self.edges.values() for x, _ in e["shape"]]
        ys = [y for e in self.edges.values() for _, y in e["shape"]]
        self.bounds = (min(xs), min(ys), max(xs), max(ys)) if xs else (0, 0, 1, 1)

    def reverse_of(self, e):
        d = self.edges.get(e)
        if not d:
            return None
        for o, x in self.edges.items():
            if x["from"] == d["to"] and x["to"] == d["from"]:
                return o
        return None

    def incoming(self, junction):
        return sorted(e for e, d in self.edges.items() if d["to"] == junction)

    def direction(self, edge, junction):
        """N/E/S/W of a road coming into a junction (where the road starts)."""
        j = self.junctions[junction]
        x, y = self.edges[edge]["shape"][0]
        dx, dy = x - j["x"], y - j["y"]
        if abs(dy) > abs(dx):
            return "N" if dy > 0 else "S"
        return "E" if dx > 0 else "W"

    def name(self, e):
        """Friendly name for the public dashboard."""
        d = self.edges[e]
        if e in self.entries or e in self.exits:
            node = d["from"] if e in self.entries else d["to"]
            x, y = self.junctions[node]["x"], self.junctions[node]["y"]
            x0, y0, x1, y1 = self.bounds
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            side = (("North" if y > cy else "South") if abs(y - cy) / max(y1 - y0, 1) > abs(x - cx) / max(x1 - x0, 1)
                    else ("East" if x > cx else "West"))
            return f"{side} {'entry' if e in self.entries else 'exit'} ({e})"
        return f"Road {e}"

    def best_route(self, start, goal, times, blocked=None):
        """Least-congested route: Dijkstra with the live travel time of every road."""
        import heapq
        if start not in self.edges or goal not in self.edges:
            return None, None
        blocked = set(blocked or ())
        dist, prev = {start: times.get(start, 0)}, {}
        heap = [(dist[start], start)]
        while heap:
            d, e = heapq.heappop(heap)
            if e == goal:
                path = [e]
                while e in prev:
                    e = prev[e]
                    path.append(e)
                return path[::-1], d
            if d > dist.get(e, 1e18):
                continue
            for n in self.next.get(e, ()):
                if n in blocked:
                    continue
                nd = d + times.get(n, self.edges[n]["length"] / max(self.edges[n]["speed"], 0.1))
                if nd < dist.get(n, 1e18):
                    dist[n], prev[n] = nd, e
                    heapq.heappush(heap, (nd, n))
        return None, None


def pick_place_edges(net, places=PLACES):
    """Same roads every time (used by city.py AND make_traffic.py)."""
    used = set()
    for p in places:
        if p.get("edge") in net.edges:
            used |= {p["edge"], net.reverse_of(p["edge"])}
    for p in places:
        if p.get("edge") in net.edges:
            continue
        inner = [e for e, d in net.edges.items() if e not in used
                 and d["from"] not in net.fringe and d["to"] not in net.fringe]
        cands = inner or [e for e in net.edges if e not in used] or list(net.edges)
        cands.sort(key=lambda e: (-net.edges[e]["length"], e))
        p["edge"] = cands[0]
        used |= {cands[0], net.reverse_of(cands[0])}
    return places


def camera_roads(net):
    """Which junction the video shows and which SUMO road is N/E/S/W."""
    j = CAMERA_JUNCTION
    if j not in net.junctions:
        cands = net.tls or list(net.junctions)
        j = max(cands, key=lambda k: (len(net.incoming(k)), k == cands[0])) if cands else None
    if CAMERA_ROADS:
        return j, dict(CAMERA_ROADS)
    roads = {}
    for e in net.incoming(j) if j else []:
        roads.setdefault(net.direction(e, j), e)
    return j, roads


def predicted_windows(forecast, name, day=DEMO_DAY):
    if not forecast:
        return []
    return [tuple(w) for w in forecast.get("peaks", {}).get(day, {}).get(name, [])]


def midpoint(shape):
    total = sum(math.dist(shape[i], shape[i + 1]) for i in range(len(shape) - 1))
    half, run = total / 2, 0
    for i in range(len(shape) - 1):
        seg = math.dist(shape[i], shape[i + 1])
        if run + seg >= half and seg > 0:
            f = (half - run) / seg
            (x1, y1), (x2, y2) = shape[i], shape[i + 1]
            return x1 + f * (x2 - x1), y1 + f * (y2 - y1), (x2 - x1) / seg, (y2 - y1) / seg
        run += seg
    (x1, y1), (x2, y2) = shape[0], shape[-1]
    d = math.dist(shape[0], shape[-1]) or 1
    return (x1 + x2) / 2, (y1 + y2) / 2, (x2 - x1) / d, (y2 - y1) / d


def building_spot(net, edge):
    """Centre and half-size of the building drawn beside a road."""
    d = net.edges[edge]
    s = max(6, min(15, d["length"] * 0.15))
    x, y, dx, dy = midpoint(d["shape"])
    nx, ny = dy, -dx
    rev = net.reverse_of(edge)
    if rev:
        rx, ry, _, _ = midpoint(net.edges[rev]["shape"])
        if (rx - x) * nx + (ry - y) * ny > 0:
            nx, ny = -nx, -ny
    gap = s + 5                                   # lane 0 is the outer lane
    return x + nx * gap, y + ny * gap, s
