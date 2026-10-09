"""Intelligent traffic control for your whole SUMO network.

    python city.py fixed                    baseline: fixed-time signals, no action
    python city.py smart                    our system
    python city.py smart --yolo             ...traffic follows the camera (yolo_counts.json)
    python city.py smart --predict          ...prepares for the predicted school/office rush
    python city.py smart --yolo --predict   everything
    add --nogui on a cloud server (no screen)
    python both.py [--yolo] [--predict]     baseline LEFT + our system RIGHT, together

All settings are in settings.py.
"""
import collections
import csv
import datetime as dt
import math
import os
import random
import re
import sys
import time

import traci

import settings as S

# ---------------------------------------------------------------- options
MODE = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else "fixed"
FLAGS = set(a for a in sys.argv[1:] if a in ("--yolo", "--predict", "--nogui"))
OPTS = {}
args = [a for a in sys.argv[1:] if a not in FLAGS and a != MODE]
for k, v in zip(args[::2], args[1::2]):
    OPTS[k] = v
USE_YOLO = "--yolo" in FLAGS
USE_PREDICT = "--predict" in FLAGS and MODE == "smart"
NOGUI = "--nogui" in FLAGS or os.environ.get("NOGUI") == "1"   # cloud server: no screen
START_AT = float(OPTS["--start-at"]) if "--start-at" in OPTS else None
DAY = OPTS.get("--day", S.DEMO_DAY)                 # which day of the week to simulate (Mon..Sun)
DELAY = S.YOLO_DELAY if USE_YOLO else S.DELAY
os.chdir(S.HERE)

net = S.Net(S.NET)
places = S.pick_place_edges(net, [dict(p) for p in S.PLACES])
forecast = S.read_json("forecast.json") if USE_PREDICT else None
if USE_PREDICT and not forecast:
    print("No forecast.json - run: python history.py  and  python predict.py   (continuing without prediction)")
    USE_PREDICT = False

# ---------------------------------------------------------------- start SUMO
route_files = [f for f in (S.VTYPES,) if os.path.exists(f)]
if (not USE_YOLO or S.YOLO_BACKGROUND) and os.path.exists(S.ROUTES):
    route_files.append(S.ROUTES)      # with --yolo it is held back (see yolo_step) while the camera is in charge
if not route_files:
    sys.exit("No traffic files. Run: python make_traffic.py")
cmd = ["sumo" if NOGUI else "sumo-gui", "-n", S.NET, "-r", ",".join(route_files), "--start",
       "--delay", "0",                                  # speed is controlled here (pause/slow/jump)
       "--ignore-route-errors", "true", "--time-to-teleport", "300",
       "--device.emissions.probability", "1",
       "--tripinfo-output", f"tripinfo_{MODE}.xml"]
if NOGUI:                                            # plain sumo has no --start / --delay
    for opt, n in (("--start", 1), ("--delay", 2)):
        i = cmd.index(opt)
        del cmd[i:i + n]
if "--pos" in OPTS and not NOGUI:
    cmd += ["--window-pos", OPTS["--pos"]]
if "--size" in OPTS and not NOGUI:
    cmd += ["--window-size", OPTS["--size"]]
traci.start(cmd)
clock = S.clock

# label above the map: BASELINE / OUR SYSTEM
VIEW_ALL = None
try:
    x0, y0, x1, y1 = net.bounds
    lw = (x1 - x0) * 0.6
    lh = lw * 260 / 1200
    lbl = os.path.abspath(f"label_{MODE}.png")
    if os.path.exists(lbl):
        traci.poi.add("mode_label", (x0 + x1) / 2, y1 + lh / 2 + 15, (255, 255, 255, 255),
                      poiType=MODE, layer=20, imgFile=lbl, width=lw, height=lh)
        VIEW_ALL = (x0 - 20, y0 - 20, x1 + 20, y1 + lh + 35)
        if not NOGUI:
            traci.gui.setBoundary("View #0", *VIEW_ALL)
except traci.TraCIException:
    pass

# ---------------------------------------------------------------- alerts
alerts = collections.deque(maxlen=40)


def alert(t, kind, text):
    print(f"[{clock(t)}] {kind}: {text}")
    alerts.appendleft({"clock": clock(t), "t": int(t), "kind": kind, "text": text})
    stage(t, kind, text)


# ---------------------------------------------------------------- demo control (pause / slow / jump)
# demo_control.json is shared by BOTH windows, so they pause, slow down and jump together.
# It is changed by: this file (auto-pause at a new stage), demo_control.py (keyboard),
# and the operator dashboard (Demo buttons).
demo = {"seen": set(), "focus_seq": 0, "ctl": {}, "mtime": None}
WORD = re.compile(r"-?[A-Za-z0-9_.]+")


def ctl():
    """Latest demo_control.json (re-read only when the file changed)."""
    try:
        m = os.path.getmtime(os.path.join(S.HERE, S.DEMO_FILE))
    except OSError:
        return {}
    if m != demo["mtime"]:
        demo["mtime"] = m
        demo["ctl"] = S.read_json(S.DEMO_FILE, {}) or {}
    return demo["ctl"]


def ctl_update(**kw):
    c = dict(S.read_json(S.DEMO_FILE, {}) or {})
    c.update(kw)
    S.write_json(S.DEMO_FILE, c)
    demo["mtime"] = None


def stage(t, kind, text):
    """OUR SYSTEM window: something worth showing happened -> pause both windows and zoom there."""
    if MODE != "smart" or kind not in S.AUTO_PAUSE or t < 5:
        return
    c = ctl()
    if not c.get("ff_next"):
        if c.get("no_autopause") or (S.AUTO_PAUSE_ONCE and kind in demo["seen"]):
            return
    demo["seen"].add(kind)
    where = next((w for w in WORD.findall(text) if w in net.edges), None)
    txt = f"{kind}: {text}"
    if c.get("hold_at") == t and c.get("stage"):        # two stages in the same second: show both
        txt = c["stage"] + "  |  " + txt
    upd = {"stage": txt, "stage_clock": clock(t), "slow_until": t + S.SLOW_FOR,
           "hold_at": t, "ff_next": False, "ff_to": None, "paused": False}
    if S.FOCUS_ON_STAGE and where:
        upd["focus"] = {"edge": where, "seq": (c.get("focus") or {}).get("seq", 0) + 1}
    ctl_update(**upd)
    print("\n" + "=" * 72 + f"\n  STAGE [{clock(t)}]  {kind}\n  {text}\n"
          "  PAUSED - press Resume in the dashboard (http://localhost:8600)\n" + "=" * 72)


def apply_focus(c):
    f = c.get("focus") or {}
    if f.get("seq", 0) == demo["focus_seq"]:
        return
    demo["focus_seq"] = f.get("seq", 0)
    if NOGUI:
        return
    try:
        e = f.get("edge")
        if e in net.edges:
            xs, ys = zip(*net.edges[e]["shape"])
            cx, cy, r = sum(xs) / len(xs), sum(ys) / len(ys), S.FOCUS_SIZE / 2
            traci.gui.setBoundary("View #0", cx - r, cy - r, cx + r, cy + r)
        else:                                           # whole map
            x0, y0, x1, y1 = net.bounds
            traci.gui.setBoundary("View #0", *(VIEW_ALL or (x0 - 20, y0 - 20, x1 + 20, y1 + 20)))
    except traci.TraCIException:
        pass


def edge_of(lane):
    return lane.rsplit("_", 1)[0]


# ---------------------------------------------------------------- signals
class Junction:
    def __init__(self, tls):
        self.id = tls
        logic = traci.trafficlight.getAllProgramLogics(tls)[0]
        self.phases = [p.state for p in logic.phases]
        links = traci.trafficlight.getControlledLinks(tls)
        self.greens, self.yellow_after = {}, {}
        n = len(self.phases)
        for i, s in enumerate(self.phases):
            if ("G" in s or "g" in s) and "y" not in s:
                lanes = {links[k][0][0] for k, c in enumerate(s)
                         if c in "Gg" and k < len(links) and links[k]}
                if lanes:
                    self.greens[i] = lanes
            if "y" in self.phases[(i + 1) % n]:
                self.yellow_after[i] = (i + 1) % n
        self.lanes = sorted({ln for v in self.greens.values() for ln in v})
        self.start, self.target, self.until, self.hold_until = 0, None, 0, -1
        self.switches = 0

    def go(self, t, phase, why, quiet=False):
        ph = traci.trafficlight.getPhase(self.id)
        y = self.yellow_after.get(ph)
        if not quiet:
            print(f"[{clock(t)}] {self.id}: SWITCH -> phase {phase} ({why})")
        self.switches += 1
        if y is not None and ph != phase:
            traci.trafficlight.setPhase(self.id, y)
            traci.trafficlight.setPhaseDuration(self.id, S.YELLOW)
            self.target, self.until = phase, t + S.YELLOW
        else:
            traci.trafficlight.setPhase(self.id, phase)
            traci.trafficlight.setPhaseDuration(self.id, 9999)
            self.start = t

    def force(self, t, road, seconds):
        for i, lanes in self.greens.items():
            if any(edge_of(ln) == road for ln in lanes):
                self.go(t, i, f"OPERATOR: green for {road} {seconds}s")
                self.hold_until = t + S.YELLOW + seconds
                return True
        return False

    def green_roads(self):
        ph = traci.trafficlight.getPhase(self.id)
        return sorted({edge_of(ln) for ln in self.greens.get(ph, ())})

    def step(self, t, weights):
        tl = traci.trafficlight
        if self.target is not None:                      # finishing a yellow
            if t >= self.until:
                tl.setPhase(self.id, self.target)
                tl.setPhaseDuration(self.id, 9999)
                self.start, self.target = t, None
            return
        if t < self.hold_until:                          # operator override running
            return
        ph = tl.getPhase(self.id)
        if ph not in self.greens:
            return
        green_for = t - self.start
        if green_for < S.EARLY_GREEN:
            return

        def w(ln):
            return weights.get(edge_of(ln), 1.0)

        waiting = {i: sum(traci.lane.getLastStepHaltingNumber(ln) * w(ln) for ln in lanes)
                   for i, lanes in self.greens.items() if i != ph}
        if not waiting:
            return
        best = max(waiting, key=waiting.get)
        now = sum(traci.lane.getLastStepVehicleNumber(ln) * w(ln) for ln in self.greens[ph])
        why = None
        if now <= S.LOW_TRAFFIC and waiting[best] > 0 and (now == 0 or waiting[best] >= now + 2):
            why = f"only {now:.0f} vehicles on green road, {waiting[best]:.0f} waiting - cut green early"
        elif green_for >= S.MIN_GREEN and waiting[best] > S.THRESHOLD and waiting[best] > now:
            why = f"{waiting[best]:.0f} waiting vs {now:.0f} on green road"
        elif green_for >= S.MAX_GREEN and waiting[best] > 0:
            why = f"max green {S.MAX_GREEN}s reached"
        if why:
            self.go(t, best, why + f", green was {int(green_for)}s")


junctions = {tls: Junction(tls) for tls in traci.trafficlight.getIDList()}
junctions = {k: j for k, j in junctions.items() if j.greens}
all_edges = [e for e in traci.edge.getIDList() if not e.startswith(":")]
print("Mode:", MODE, "| options:", " ".join(sorted(FLAGS)) or "none")
print("Signal junctions:", list(junctions))
if not junctions:
    print("WARNING: no traffic lights found. In netedit set the crossings to traffic_light.")

# ---------------------------------------------------------------- buildings
for i, p in enumerate(places):
    bx, by, s = S.building_spot(net, p["edge"])
    shape = [(bx - s, by - s), (bx + s, by - s), (bx + s, by + s), (bx - s, by + s), (bx - s, by - s)]
    traci.polygon.add(f"bld_{i}", shape, p["color"], fill=True, polygonType=p["name"], layer=-1)
    img = os.path.abspath(p["img"]) if p.get("img") and os.path.exists(p["img"]) else ""
    traci.poi.add(f"lbl_{i}", bx, by, (255, 255, 255, 255), poiType=p["name"], layer=10,
                  imgFile=img, width=2 * s, height=2 * s)
    p["xy"] = (bx, by)
    print(f"Building: {p['name']} beside road {p['edge']}" + ("" if img else f"  (image {p.get('img')} not found)"))

# ---------------------------------------------------------------- priorities
override = {p["name"]: None for p in places}        # operator: "on" / "off" / None


def place_state(p, t):
    now = S.sec_of_day(t)
    scheduled = S.in_windows(now, p["times"])
    pw = S.predicted_windows(forecast, p["name"], DAY) if USE_PREDICT else []
    predicted = S.in_windows(now, pw, before=S.PREPARE_MIN * 60)
    ov = override[p["name"]] if MODE == "smart" else None
    on = ov == "on" or (ov != "off" and (scheduled or predicted))
    return {"on": on, "scheduled": scheduled, "predicted": predicted, "override": ov, "windows": pw}


def edge_weights(t):
    w = {}
    for p in places:
        if place_state(p, t)["on"]:
            for e in (p["edge"], net.reverse_of(p["edge"])):
                if e:
                    w[e] = max(w.get(e, 1.0), p["weight"])
    return w


normal_speed = {e: traci.lane.getMaxSpeed(e + "_0") for e in all_edges}
last_on, announced = None, set()
advisory_restore = {}


def advisory_effects_now():
    try:
        items = S.read_json("advisories.json", []) or []
    except Exception:
        return []
    now = dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))
    out = []
    for item in items:
        status = str(item.get("status") or "draft").lower()
        if status != "published":
            continue
        start = item.get("start")
        end = item.get("end")
        try:
            start_dt = dt.datetime.fromisoformat(str(start).replace("Z", "+00:00")) if start else None
            end_dt = dt.datetime.fromisoformat(str(end).replace("Z", "+00:00")) if end else None
            if start_dt is None:
                continue
            if start_dt.tzinfo is None:
                start_dt = start_dt.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
            if end_dt is not None and end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
            if start_dt <= now and (end_dt is None or now < end_dt):
                out.append(item)
        except ValueError:
            continue
    return out


def apply_advisory_controls(t):
    if MODE != "smart":
        return
    active = advisory_effects_now()
    active_roads = {str(item.get("road_id") or "").strip() for item in active if str(item.get("road_id") or "").strip()}
    for road in list(advisory_restore):
        if road not in active_roads:
            info = advisory_restore.pop(road)
            try:
                traci.edge.setMaxSpeed(road, info["speed"])
                traci.edge.adaptTraveltime(road, info["tt"])
            except traci.TraCIException:
                pass
    for item in active:
        road = str(item.get("road_id") or "").strip()
        if not road or road not in net.edges:
            continue
        if road not in advisory_restore:
            try:
                advisory_restore[road] = {"speed": traci.lane.getMaxSpeed(road + "_0"), "tt": traci.edge.getTraveltime(road)}
            except traci.TraCIException:
                advisory_restore[road] = {"speed": 0, "tt": 1.0}
        closure = str(item.get("closure") or "info").lower()
        try:
            if closure == "full":
                traci.edge.setMaxSpeed(road, 0.1)
                traci.edge.adaptTraveltime(road, 1e6)
                rerouted = 0
                for v in traci.vehicle.getIDList():
                    try:
                        route = traci.vehicle.getRoute(v)
                        idx = traci.vehicle.getRouteIndex(v)
                        if not route or idx < 0 or road not in route[idx:]:
                            continue
                        cur = traci.vehicle.getRoadID(v)
                        if cur.startswith(":") or cur == road:
                            continue
                        r = traci.simulation.findRoute(cur, route[-1], vType=traci.vehicle.getTypeID(v))
                        if r.edges and road not in r.edges:
                            traci.vehicle.setRoute(v, list(r.edges))
                            rerouted += 1
                    except traci.TraCIException:
                        pass
                if rerouted:
                    alert(t, "ADVISORY", f"{item.get('type', 'Advisory')} on {road} - road closed, {rerouted} vehicles rerouted")
            elif closure in ("partial", "slow"):
                traci.edge.setMaxSpeed(road, 15 / 3.6)
                traci.edge.adaptTraveltime(road, 50)
                if closure == "partial":
                    try:
                        traci.lane.setMaxSpeed(road + "_0", 3)
                    except traci.TraCIException:
                        pass
                if item.get("type"):
                    alert(t, "ADVISORY", f"{item.get('type')} on {road} - {closure} traffic")
        except traci.TraCIException:
            pass


def update_priorities(t):
    global last_on
    states = {p["name"]: place_state(p, t) for p in places}
    on = sorted(n for n, s in states.items() if s["on"])
    if on != last_on:
        alert(t, "PRIORITY", f"active: {', '.join(on) if on else 'none'}")
        for e, v in normal_speed.items():
            traci.edge.setMaxSpeed(e, v)
        for p in places:
            if states[p["name"]]["on"] and "speed_kmh" in p:
                for e in (p["edge"], net.reverse_of(p["edge"])):
                    if e in normal_speed:
                        traci.edge.setMaxSpeed(e, p["speed_kmh"] / 3.6)
                alert(t, "ADVISORY", f"{p['name']} zone on {p['edge']}: speed limit {p['speed_kmh']} km/h")
        last_on = on
    if USE_PREDICT:                                     # tell when the prediction starts preparing
        now = S.sec_of_day(t)
        for p in places:
            for a, b in states[p["name"]]["windows"]:
                key = (p["name"], a)
                if key not in announced and S.hhmm(a) - S.PREPARE_MIN * 60 <= now <= S.hhmm(b):
                    announced.add(key)
                    alert(t, "PREDICTED", f"{p['name']} rush expected {a}-{b} on {p['edge']} - "
                                          f"preparing signals and diversions now")
    return states


# ---------------------------------------------------------------- least-congestion recommendation
def road_time(e):
    tt = traci.edge.getTraveltime(e)
    free = traci.lane.getLength(e + "_0") / max(traci.lane.getMaxSpeed(e + "_0"), 0.1)
    return min(max(tt, free), 600)


def level(h):
    return "HIGH" if h > S.JAM_LIMIT else "MEDIUM" if h > S.CONGESTED else "LOW"


approval = S.APPROVAL
recs, rec_id, quiet = [], 0, {}
diverted = set()
rng = random.Random(1)
rec_file = open(f"recommendations_{MODE}.csv", "w", newline="")
rec_log = csv.writer(rec_file)
rec_log.writerow(["clock", "id", "congested_road", "waiting", "vehicles_affected", "old_route_s",
                  "best_route", "best_route_s", "saving_s", "status", "diverted"])


def apply_rec(t, rec):
    """Divert a share of the vehicles still heading to the congested road (route found again from
    where each vehicle is NOW, so it also works after the operator took time to approve)."""
    live = set(traci.vehicle.getIDList())
    n = 0
    for v, dest in rec["vehicles"]:
        if v not in live or v in diverted or rng.random() >= S.DIVERT_SHARE:
            continue
        try:
            cur = traci.vehicle.getRoadID(v)
            rest = traci.vehicle.getRoute(v)[traci.vehicle.getRouteIndex(v):]
            if cur.startswith(":") or cur == rec["road"] or rec["road"] not in rest:
                continue
            r = traci.simulation.findRoute(cur, dest, vType=traci.vehicle.getTypeID(v))
            if r.edges and rec["road"] not in r.edges:
                traci.vehicle.setRoute(v, list(r.edges))
                traci.vehicle.setColor(v, (200, 0, 255, 255))      # purple = diverted
                diverted.add(v)
                n += 1
        except traci.TraCIException:
            pass
    rec["status"], rec["diverted"] = "applied", n
    alert(t, "DIVERT", f"#{rec['id']}: {n} vehicles moved from {rec['road']} to {rec['route']}")


def recommend(t):
    global rec_id
    times = {e: road_time(e) for e in all_edges}
    for e, v in times.items():
        traci.edge.adaptTraveltime(e, v)
    busy = sorted((e for e in all_edges if traci.edge.getLastStepHaltingNumber(e) > S.CONGESTED),
                  key=lambda e: -traci.edge.getLastStepHaltingNumber(e))
    for bad in busy:
        affected = []
        for v in traci.vehicle.getIDList():
            cur = traci.vehicle.getRoadID(v)
            if cur.startswith(":") or cur == bad:
                continue
            route = traci.vehicle.getRoute(v)
            idx = traci.vehicle.getRouteIndex(v)
            if bad in route[idx + 1:]:
                affected.append((v, cur, route[idx:]))
        if not affected:
            continue
        best = {}
        for v, cur, rest in affected:
            key = (cur, rest[-1])
            if key not in best:
                r = traci.simulation.findRoute(cur, rest[-1], vType=traci.vehicle.getTypeID(v))
                old_t = sum(times.get(e, 0) for e in rest)
                new_t = sum(times.get(e, 0) for e in r.edges) if r.edges else 1e9
                ok = r.edges and bad not in r.edges and old_t - new_t >= S.MIN_SAVING
                best[key] = (list(r.edges), old_t, new_t) if ok else None
        good = [(v, rest[-1]) for v, cur, rest in affected if best[(cur, rest[-1])]]
        halt = traci.edge.getLastStepHaltingNumber(bad)
        if not good:
            if t - quiet.get(bad, -1e9) >= 120:          # say it at most every 2 minutes per road
                quiet[bad] = t
                alert(t, "CONGESTION", f"{bad} ({halt} waiting) - no faster route, signals will handle it")
            continue
        first = next(best[k] for k in best if best[k])
        edges, old_t, new_t = first
        path = " > ".join(edges)
        old = next((r for r in recs if r["road"] == bad and r["status"] == "pending"), None)
        if old:                                         # same jam still waiting for the operator
            old.update(vehicles=good, affected=len(affected), waiting=halt, clock=clock(t))
            continue
        rec_id += 1
        rec = {"id": rec_id, "clock": clock(t), "road": bad, "waiting": halt, "affected": len(affected),
               "route": path, "saving_s": round(old_t - new_t), "status": "advice", "diverted": 0,
               "vehicles": good}
        recs.insert(0, rec)
        del recs[30:]
        alert(t, "RECOMMEND", f"#{rec_id} {bad} congested ({halt} waiting, {len(affected)} heading there). "
                              f"Least congested route: {path} (saves {old_t - new_t:.0f} s)")
        if MODE == "smart":
            if approval == "auto":
                apply_rec(t, rec)
            else:
                rec["status"] = "pending"
                alert(t, "OPERATOR", f"#{rec_id} waiting for approval in the operator dashboard")
        rec_log.writerow([clock(t), rec_id, bad, halt, len(affected), round(old_t), path, round(new_t),
                          round(old_t - new_t), rec["status"], rec["diverted"]])
    rec_file.flush()


# ---------------------------------------------------------------- predicted diversion
# From the 14-day history we know WHEN the school / office road jams on WHICH day (forecast.json).
# During that time (and PREPARE_MIN minutes before) vehicles coming behind, whose route passes
# that road, are sent another way BEFORE they reach the crowd. Vehicles going TO the school /
# office itself are left alone.
pre_done, pre_said = set(), set()


def pre_divert(t):
    now = S.sec_of_day(t)
    for p in places:
        wins = [w for w in S.predicted_windows(forecast, p["name"], DAY)
                if S.in_windows(now, [w], before=S.PREPARE_MIN * 60)]
        if not wins or not p.get("edge"):
            continue
        a, b = wins[0]
        bad = {e for e in (p["edge"], net.reverse_of(p["edge"])) if e}
        n, via = 0, ""
        for v in traci.vehicle.getIDList():
            if v in pre_done or v in diverted:
                continue
            try:
                cur = traci.vehicle.getRoadID(v)
                if cur.startswith(":") or cur in bad:
                    continue
                rest = traci.vehicle.getRoute(v)[traci.vehicle.getRouteIndex(v):]
                if not any(e in bad for e in rest[1:]) or rest[-1] in bad:
                    continue
                pre_done.add(v)
                if rng.random() >= S.PRE_DIVERT_SHARE:          # not every driver follows the advice
                    continue
                for e in bad:
                    traci.vehicle.setAdaptedTraveltime(v, e, 1e6)
                traci.vehicle.rerouteTraveltime(v, False)
                new = traci.vehicle.getRoute(v)[traci.vehicle.getRouteIndex(v):]
                if any(e in bad for e in new[1:]):
                    continue                                     # no other way for this one
                if not v.startswith("cam"):
                    traci.vehicle.setColor(v, S.PRE_DIVERT_COLOR)
                n += 1
                via = " > ".join(new)
            except traci.TraCIException:
                pass
        key = (p["name"], a)
        if n and key not in pre_said:
            pre_said.add(key)
            alert(t, "PRE-DIVERT", f"{p['name']} rush predicted {a}-{b} ({DAY}) on {p['edge']}: "
                                   f"vehicles coming behind are sent another way, e.g. {via}")
        if n:
            print(f"[{clock(t)}] pre-divert {p['name']}: {n} more vehicles avoid {p['edge']}")
            if recs and recs[0].get("kind") == "pre" and recs[0]["road"] == p["edge"]:
                recs[0]["diverted"] += n
            else:
                recs.insert(0, {"id": 0, "kind": "pre", "clock": clock(t), "road": p["edge"], "waiting": 0,
                                "affected": n, "route": via, "saving_s": 0, "status": "applied", "diverted": n})
                del recs[30:]


# ---------------------------------------------------------------- operator commands
cmds = S.read_json(S.COMMANDS, []) or []
last_cmd = max([c.get("id", 0) for c in cmds], default=0)       # ignore old commands


def operator_commands(t):
    global last_cmd, approval
    for c in S.read_json(S.COMMANDS, []) or []:
        if c.get("id", 0) <= last_cmd:
            continue
        last_cmd = c["id"]
        kind = c.get("cmd")
        if kind in ("approve", "reject"):
            rec = next((r for r in recs if r["id"] == c.get("rec")), None)
            if rec and rec["status"] == "pending":
                if kind == "approve":
                    apply_rec(t, rec)
                else:
                    rec["status"] = "rejected"
                    alert(t, "OPERATOR", f"#{rec['id']} rejected")
        elif kind == "approval":
            approval = c.get("value", "auto")
            alert(t, "OPERATOR", f"approval mode: {approval}")
        elif kind == "force_green":
            j = junctions.get(c.get("junction"))
            if j and j.force(t, c.get("road"), int(c.get("seconds", 30))):
                alert(t, "OPERATOR", f"{j.id}: green for {c.get('road')} for {c.get('seconds', 30)} s")
        elif kind == "place":
            if c.get("name") in override:
                override[c["name"]] = c.get("value")
                alert(t, "OPERATOR", f"{c['name']} priority: {c.get('value') or 'automatic'}")


# ---------------------------------------------------------------- camera (YOLO)
TYPE_MAP = {"car": "car", "bike": "bike", "motorcycle": "bike", "auto": "auto", "bus": "bus", "truck": "truck"}
cam_j, cam_roads = S.camera_roads(net) if USE_YOLO else (None, {})
yolo = {"rates": {}, "last": None, "counts": {}, "n": 0, "rng": random.Random(42), "routes": {}, "warned": False,
        "phase": "waiting", "seen_at": None, "started": time.time(), "got": False}
_old = S.read_json(S.YOLO_FILE) if USE_YOLO else None
if _old:
    yolo["last"] = _old.get("time")            # a file left from an earlier run is not live input
if USE_YOLO:
    print(f"Camera junction: {cam_j}   roads: {cam_roads}   (change in settings.py if wrong)")


def yolo_route(edge):
    """Route from a camera road to a random exit (same choice in fixed and smart)."""
    opts = yolo["routes"].get(edge)
    if opts is None:
        opts = []
        rev = net.reverse_of(edge)
        for ex in net.exits:
            if ex == rev:
                continue
            r = traci.simulation.findRoute(edge, ex)
            if r.edges:
                rid = f"yr_{edge}_{ex}"
                traci.route.add(rid, list(r.edges))
                opts.append(rid)
        yolo["routes"][edge] = opts
    return yolo["rng"].choice(opts) if opts else None


def poisson(lam, r):
    if lam <= 0:
        return 0
    k, p, l = 0, 1.0, math.exp(-lam)
    while True:
        p *= r.random()
        if p <= l:
            return k
        k += 1


def add_vehicle(edge, vtype):
    rid = yolo_route(edge)
    if not rid:
        return
    yolo["n"] += 1
    try:
        traci.vehicle.add(f"cam{yolo['n']}", rid, typeID=vtype, departLane="best", departSpeed="max")
        traci.vehicle.setColor(f"cam{yolo['n']}", S.CAMERA_COLOR)     # camera vehicles stand out in SUMO
    except traci.TraCIException:
        pass


def camera_stopped(t, why):
    yolo["rates"] = {}
    gone = 0
    for v in traci.vehicle.getIDList():                 # camera off -> its (magenta) vehicles disappear
        if v.startswith("cam"):
            try:
                traci.vehicle.remove(v)
                gone += 1
            except traci.TraCIException:
                pass
    why += f" ({gone} camera vehicles removed)"
    if S.YOLO_BACKGROUND == "after":
        yolo["phase"] = "normal"
        alert(t, "CAMERA", f"{why} - normal traffic starts now")
    else:
        yolo["phase"] = "stopped"
        alert(t, "CAMERA", why)


def read_yolo(t):
    data = S.read_json(S.YOLO_FILE) or {}
    new = bool(data) and data.get("time") != yolo["last"]
    if new:
        yolo["last"] = data.get("time")
        yolo["seen_at"] = time.time()
    if data.get("ended"):                              # YOLO was closed / the video finished
        if new and yolo["phase"] == "camera":
            camera_stopped(t, "camera input ended")
        return
    if not new:
        win = float(data.get("window_s", 30) or 30)
        if yolo["phase"] == "camera" and time.time() - yolo["seen_at"] > max(S.YOLO_TIMEOUT, 2.5 * win):
            camera_stopped(t, "no new camera counts")
        elif yolo["phase"] == "waiting":
            if not yolo["warned"]:
                alert(t, "CAMERA", f"waiting for YOLO (start main_linked.py or: python fake_yolo.py)")
                yolo["warned"] = True
            if S.YOLO_BACKGROUND == "after" and time.time() - yolo["started"] > S.YOLO_WAIT:
                camera_stopped(t, "no camera input")
        return
    if yolo["phase"] != "camera":
        alert(t, "CAMERA", "camera input live - SUMO now follows the camera")
        yolo["phase"] = "camera"
        yolo["live_at"] = t
    win = float(data.get("window_s", 30)) or 30
    first = not yolo["got"]
    yolo["got"] = True
    yolo["counts"] = data.get("roads", {})
    for road, edge in cam_roads.items():
        m = data.get("roads", {}).get(road)
        if not m:
            continue
        types = {TYPE_MAP[k]: float(v) for k, v in m.items() if k in TYPE_MAP}
        if not types:
            types = {"car": float(m.get("count", 0))}
        yolo["rates"][edge] = {vt: c * S.YOLO_SCALE / win for vt, c in types.items()}
        if first:                                         # start SUMO with the queue seen in the video
            for _ in range(min(int(m.get("queue", 0)), 15)):
                add_vehicle(edge, "car")
    if new:
        alert(t, "CAMERA", "counts " + ", ".join(f"{r}:{m.get('count', 0)}" for r, m in data.get("roads", {}).items()))
        now = dt.datetime.now()
        slot = (S.sec_of_day(t) // 900) * 900
        new_file = not os.path.exists("yolo_history.csv")
        with open("yolo_history.csv", "a", newline="") as f:
            w = csv.writer(f)
            if new_file:
                w.writerow(["date", "day", "time", "key", "count"])
            for road, m in data.get("roads", {}).items():
                w.writerow([now.date().isoformat(), now.strftime("%a"),
                            f"{slot // 3600:02d}:{(slot % 3600) // 60:02d}", f"Camera_{road}", m.get("count", 0)])


if USE_YOLO:                                   # fix the routes now, so fixed and smart use the same ones
    for _e in cam_roads.values():
        yolo_route(_e)
    yolo["rng"] = random.Random(42)


def yolo_step():
    # "after": no normal traffic until the camera stops.  True: camera roads carry only camera vehicles.
    hold_all = S.YOLO_BACKGROUND == "after" and (yolo["phase"] == "waiting" or (
        yolo["phase"] == "camera" and traci.simulation.getTime() - yolo.get("live_at", 0) < S.YOLO_FIRST))
    if hold_all or (S.YOLO_BACKGROUND and yolo["rates"]):
        for v in traci.simulation.getDepartedIDList():
            if not v.startswith("cam"):
                try:
                    if hold_all or traci.vehicle.getRoute(v)[0] in yolo["rates"]:
                        traci.vehicle.remove(v)
                except traci.TraCIException:
                    pass
    for edge, rates in sorted(yolo["rates"].items()):
        for vt, lam in sorted(rates.items()):
            for _ in range(poisson(lam, yolo["rng"])):
                add_vehicle(edge, vt)


# ---------------------------------------------------------------- dashboard state
def write_live(t, states, kpi):
    roads = {}
    for e in all_edges:
        h = traci.edge.getLastStepHaltingNumber(e)
        roads[e] = {"halt": h, "veh": traci.edge.getLastStepVehicleNumber(e),
                    "speed_kmh": round(traci.edge.getLastStepMeanSpeed(e) * 3.6, 1),
                    "tt": round(road_time(e), 1), "level": level(h)}
    S.write_json(f"live_state_{MODE}.json", {
        "mode": MODE, "t": int(t), "clock": clock(t), "updated": time.time(), "day": DAY,
        "kpi": kpi, "approval": approval, "roads": roads,
        "junctions": {j.id: {"phase": traci.trafficlight.getPhase(j.id), "green": j.green_roads(),
                             "green_for": int(t - j.start), "override": t < j.hold_until,
                             "queues": {e: roads[e]["halt"] for e in sorted({edge_of(ln) for ln in j.lanes})
                                        if e in roads}}
                      for j in junctions.values()},
        "places": [{"name": p["name"], "edge": p["edge"], "xy": p["xy"], **{k: v for k, v in states[p["name"]].items()}}
                   for p in places],
        "alerts": list(alerts),
        "recs": [{k: v for k, v in r.items() if k != "vehicles"} for r in recs],
        "camera": {"junction": cam_j, "roads": cam_roads, "counts": yolo["counts"], "phase": yolo["phase"]} if USE_YOLO else None,
        "flags": sorted(FLAGS)})


# ---------------------------------------------------------------- main loop
timeline_file = open(f"timeline_{MODE}.csv", "w", newline="")
timeline = csv.writer(timeline_file)
timeline.writerow(["time_s", "clock", "vehicles", "waiting", "avg_speed_kmh", "arrived_total"])
arrived_total = 0
kpi = {}

if MODE == "smart":
    for j in junctions.values():
        traci.trafficlight.setPhaseDuration(j.id, 9999)
if START_AT:
    print(f"[{MODE}] ready, starting together in {max(0, START_AT - time.time()):.0f} s")
else:
    S.write_json(S.DEMO_FILE, {})                       # single window: start un-paused
pace = {"wall": START_AT or time.time(), "step": 0, "delay": DELAY}
t, states = 0, {}
last_live = 0


def smart_step():
    p = S.read_json("progress_smart.json", {}) or {}
    return p.get("step", 1e9) if time.time() - p.get("time", 0) < 5 else 1e9


try:
    for step in range(S.SIM_TIME):
        # ---- demo control: pause / stage hold / jump / slow motion (same in both windows)
        c = ctl()
        apply_focus(c)
        held = False
        while c.get("paused") or (c.get("hold_at") is not None and step >= c["hold_at"]):
            if not held:
                print(f"[{clock(t)}] PAUSED")
                held = True
            if time.time() - last_live > 2 and step:
                write_live(t, states, kpi)
                last_live = time.time()
            time.sleep(0.1)
            c = ctl()
            apply_focus(c)
        ff = c.get("ff_next") or (c.get("ff_to") is not None and step < c["ff_to"]) \
            or (c.get("hold_at") is not None and step < c["hold_at"] - 2)      # catch up to the other window
        if ff:
            delay = 0
        elif S.SLOW_DELAY and step < (c.get("slow_until") or 0):
            delay = S.SLOW_DELAY
        else:
            delay = c.get("delay") or DELAY
        if held or delay != pace["delay"]:
            pace = {"wall": time.time(), "step": step, "delay": delay}
            if held:
                print(f"[{clock(t)}] RESUMED")
        wait = pace["wall"] + (step - pace["step"]) * delay / 1000 - time.time()
        if wait > 0:
            time.sleep(wait)
        if START_AT and c.get("ff_next"):               # jumping: keep both windows on the same second
            if MODE == "smart":
                S.write_json("progress_smart.json", {"step": step, "time": time.time()})
            else:
                while step > smart_step() and ctl().get("ff_next"):
                    time.sleep(0.005)
        if USE_YOLO:
            try:
                mt = os.path.getmtime(S.YOLO_FILE)
            except OSError:
                mt = None
            if step % S.YOLO_EVERY == 0 or mt != yolo.get("mtime"):    # new counts / camera off: react at once
                yolo["mtime"] = mt
                read_yolo(step)
            yolo_step()
        traci.simulationStep()
        t = traci.simulation.getTime()
        arrived_total += traci.simulation.getArrivedNumber()

        states = update_priorities(t)
        if step % 10 == 0:
            apply_advisory_controls(t)

        if step % 10 == 0:                              # data for the comparison chart
            vs = traci.vehicle.getIDList()
            sp = sum(traci.vehicle.getSpeed(v) for v in vs) / len(vs) * 3.6 if vs else 0
            waiting = sum(traci.edge.getLastStepHaltingNumber(e) for e in all_edges)
            timeline.writerow([int(t), clock(t), len(vs), waiting, round(sp, 1), arrived_total])
            kpi = {"vehicles": len(vs), "waiting": waiting, "avg_speed_kmh": round(sp, 1),
                   "arrived": arrived_total, "diverted": len(diverted),
                   "switches": sum(j.switches for j in junctions.values())}

        if step % 30 == 0:
            jams = [e for e in all_edges if traci.edge.getLastStepHaltingNumber(e) > S.JAM_LIMIT]
            print(f"[{clock(t)}] vehicles={kpi.get('vehicles')} waiting={kpi.get('waiting')}")
            for e in jams:
                alert(t, "JAM", f"{e}: {traci.edge.getLastStepHaltingNumber(e)} vehicles waiting")

        if step % S.CHECK_EVERY == 15:
            recommend(t)
        if MODE == "smart" and USE_PREDICT and step % S.CHECK_EVERY == 5:
            pre_divert(t)

        if MODE == "smart":
            operator_commands(t)
            w = edge_weights(t)
            for j in junctions.values():
                j.step(t, w)

        if step % S.LIVE_EVERY == 0:
            write_live(t, states, kpi)
            last_live = time.time()
except traci.exceptions.FatalTraCIError:
    print("SUMO window was closed.")
finally:
    try:
        traci.close()
    except Exception:
        pass
    rec_file.close()
    timeline_file.close()
print(f"Recommendations saved in recommendations_{MODE}.csv  (vehicles diverted: {len(diverted)})")
if MODE == "smart":
    print("Signal switches:", {k: j.switches for k, j in junctions.items()})
print(f"Done. Results saved in tripinfo_{MODE}.xml")
