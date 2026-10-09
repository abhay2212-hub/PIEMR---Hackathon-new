"""Professional dashboards (no Streamlit needed, works offline).

    python dashboard_server.py

Then open in a browser:
    Start page:                http://localhost:8600
    Authority login:           http://localhost:8600/login
    Control room (operator):  http://localhost:8600/control
    Public / commuters:       http://localhost:8600/public
    On a phone (same Wi-Fi):  http://<laptop-IP>:8600/public   (the IP is printed below)

Start the simulation first (python both.py ...). The pages update every 2 seconds.
"""
import csv
import datetime as dt
import hashlib
import hmac
import json
import os
import camera_web
import select
import secrets
import socket
import threading
import time
import xml.etree.ElementTree as ET
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import settings as S

PORT = 8600
os.chdir(S.HERE)
WEB = os.path.join(S.HERE, "web")
net = S.Net(S.NET)
ADVISORIES = "advisories.json"
ADVISORY_TYPES = [
    "VIP movement",
    "Construction work",
    "Road damage / pothole",
    "Water logging",
    "Accident",
    "Event / procession",
    "Other",
]
ADVISORY_CLOSURE = {"full": "Road fully closed", "partial": "One lane closed", "slow": "Expect slow traffic", "info": "Info only"}
ADVISORY_SEVERITY = ("info", "warning", "critical")
_COMPARISON_SIGNATURE = None
_COMPARISON_DATA = None
_AUTH_LOCK = threading.RLock()
_SESSIONS = {}
_LOGIN_ATTEMPTS = {}
_SESSION_TTL = max(1, int(getattr(S, "SESSION_HOURS", 8))) * 60 * 60
_DUMMY_SALT = secrets.token_bytes(16)
_DUMMY_HASH = hashlib.pbkdf2_hmac("sha256", b"invalid-login", _DUMMY_SALT, 200000).hex()
_CAMERA_FILE_LOCK = threading.Lock()


def camera_ingest_allowed(handler):
    if session_user(handler):
        return True
    configured_key = getattr(S, "CONTROL_KEY", "")
    supplied_key = handler.headers.get("X-Control-Key", "")
    return not configured_key or hmac.compare_digest(supplied_key, configured_key)


def save_camera_file(name, data):
    path = os.path.join(S.HERE, name)
    with _CAMERA_FILE_LOCK:
        try:
            with open(path + ".tmp", "wb") as f:
                f.write(data)
            os.replace(path + ".tmp", path)
            return True
        except OSError:
            return False


def read_camera_stats():
    stats = S.read_json("camera_stats.json", {})
    if not isinstance(stats, dict):
        stats = {}
    try:
        age_s = max(0, time.time() - float(stats.get("time", 0)))
    except (TypeError, ValueError):
        age_s = 1_000_000_000
    stats["age_s"] = age_s
    stats["live"] = bool(stats.get("live")) and age_s <= 5
    return stats


def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200000)
    return f"pbkdf2$200000${salt.hex()}${digest.hex()}"


def verify_password(password, encoded):
    try:
        kind, iterations, salt_hex, expected = encoded.split("$")
        count = int(iterations)
        salt = bytes.fromhex(salt_hex)
        if kind != "pbkdf2" or count < 1 or count > 10000000 or len(salt) != 16:
            raise ValueError("invalid password hash")
    except (AttributeError, TypeError, ValueError):
        salt, count, expected = _DUMMY_SALT, 200000, _DUMMY_HASH
        valid_format = False
    else:
        valid_format = len(expected) == 64 and all(c in "0123456789abcdef" for c in expected.lower())
        if not valid_format:
            expected = _DUMMY_HASH
            salt, count = _DUMMY_SALT, 200000
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, count).hex()
    return hmac.compare_digest(actual, expected.lower()) and valid_format


def session_user(handler):
    cookie = SimpleCookie()
    try:
        cookie.load(handler.headers.get("Cookie", ""))
        token = cookie["session"].value
    except (KeyError, CookieError):
        return None
    now = time.time()
    with _AUTH_LOCK:
        session = _SESSIONS.get(token)
        if session and session[1] > now:
            return session[0]
        _SESSIONS.pop(token, None)
    return None


def make_session(user):
    token = secrets.token_urlsafe(32)
    with _AUTH_LOCK:
        _SESSIONS[token] = (user, time.time() + _SESSION_TTL)
    return token


def authority_user(handler):
    user = session_user(handler)
    if user:
        return user
    key = handler.headers.get("X-Control-Key", "")
    if getattr(S, "CONTROL_KEY", "") and hmac.compare_digest(key, S.CONTROL_KEY):
        return "netlify"
    return None


if not getattr(S, "AUTH_USERS", None):
    S.AUTH_USERS = {"admin": password_hash("admin123")}
    print("WARNING: No AUTH_USERS configured. Temporary login is admin / admin123; change it with make_user.py.")


# ---------------------------------------------------------------- data for the pages
def net_json():
    return {
        "bounds": net.bounds,
        "edges": {e: {"shape": d["shape"], "lanes": d["lanes"], "from": d["from"], "to": d["to"]}
                  for e, d in net.edges.items()},
        "tls": [{"id": j, "x": net.junctions[j]["x"], "y": net.junctions[j]["y"]} for j in net.tls
                if j in net.junctions],
        "entries": [{"id": e, "name": net.name(e)} for e in net.entries],
        "exits": [{"id": e, "name": net.name(e)} for e in net.exits],
        "reverse": {e: net.reverse_of(e) for e in net.edges},
    }


def timeline(mode, max_points=360):
    path = f"timeline_{mode}.csv"
    if not os.path.exists(path):
        return []
    try:
        with open(path) as f:
            rows = list(csv.DictReader(f))
    except OSError:
        return []
    step = max(1, len(rows) // max_points)
    out = []
    for r in rows[::step]:
        try:
            out.append([int(r["time_s"]), r["clock"], int(r["waiting"]), float(r["avg_speed_kmh"]),
                        int(r["vehicles"])])
        except (KeyError, ValueError):
            pass
    return out


def tripinfo(path):
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError:
        try:
            with open(path) as f:
                text = f.read()
        except OSError:
            return None
        end = text.rfind("</tripinfo>")
        text = text[:end + 11] if end >= 0 else text[:text.rfind("/>") + 2]
        start = text.find("<tripinfo ")
        if start < 0:
            return None
        try:
            root = ET.fromstring("<tripinfos>" + text[start:] + "</tripinfos>")
        except ET.ParseError:
            return None
    except OSError:
        return None

    out = []
    try:
        for trip in root.iter("tripinfo"):
            if trip.get("vaporized"):
                continue
            emissions = trip.find("emissions")
            out.append({
                "delay": float(trip.get("timeLoss", 0)),
                "wait": float(trip.get("waitingTime", 0)),
                "travel": float(trip.get("duration", 0)),
                "stops": float(trip.get("waitingCount", 0)),
                "co2": float(emissions.get("CO2_abs", 0)) / 1_000_000 if emissions is not None else 0,
            })
    except (TypeError, ValueError):
        return None
    return out


def comparison_summary():
    global _COMPARISON_SIGNATURE, _COMPARISON_DATA
    paths = ("tripinfo_fixed.xml", "tripinfo_smart.xml")
    try:
        signature = tuple((os.stat(path).st_mtime_ns, os.stat(path).st_size) for path in paths)
    except OSError:
        return {"available": False, "rows": []}
    if signature == _COMPARISON_SIGNATURE:
        return _COMPARISON_DATA

    baseline, smart = (tripinfo(path) for path in paths)
    if baseline is None or smart is None:
        data = {"available": False, "rows": []}
    else:
        metrics = (
            ("Vehicles finished", "count", True),
            ("Avg delay (s)", "delay", False),
            ("Avg waiting at red (s)", "wait", False),
            ("Avg travel time (s)", "travel", False),
            ("Avg stops per vehicle", "stops", False),
            ("Total CO2 (kg)", "co2", False),
        )
        rows = []
        for label, key, higher_better in metrics:
            fixed_value = len(baseline) if key == "count" else (
                sum(trip[key] for trip in baseline) if key == "co2" else
                sum(trip[key] for trip in baseline) / len(baseline) if baseline else 0)
            smart_value = len(smart) if key == "count" else (
                sum(trip[key] for trip in smart) if key == "co2" else
                sum(trip[key] for trip in smart) / len(smart) if smart else 0)
            change = (smart_value - fixed_value) / fixed_value * 100 if fixed_value else None
            rows.append({"label": label, "baseline": fixed_value, "smart": smart_value,
                         "change_pct": change, "higher_better": higher_better})
        data = {"available": True, "rows": rows, "updated": max(pair[0] for pair in signature) / 1e9}

    _COMPARISON_SIGNATURE = signature
    _COMPARISON_DATA = data
    return data


def state():
    smart = S.read_json("live_state_smart.json")
    fixed = S.read_json("live_state_fixed.json")
    now = time.time()
    frame_path = os.path.join(S.HERE, "camera_frame.jpg")
    try:
        camera_frame_updated = os.path.getmtime(frame_path)
    except OSError:
        camera_frame_updated = 0
    return {
        "smart": smart, "fixed": fixed,
        "advisories": S.read_json(ADVISORIES, []) or [],
        "camera_frame_updated": camera_frame_updated,
        "comparison": comparison_summary(),
        "smart_live": bool(smart) and now - smart.get("updated", 0) < 10,
        "fixed_live": bool(fixed) and now - fixed.get("updated", 0) < 10,
        "demo": S.read_json(S.DEMO_FILE, {}) or {},
        "settings": {"start_clock": S.START_CLOCK, "sim_time": S.SIM_TIME, "delay": S.DELAY,
                     "threshold": S.THRESHOLD, "low_traffic": S.LOW_TRAFFIC, "congested": S.CONGESTED},
        "has_comparison": os.path.exists("comparison.png"),
    }


def route(a, b):
    smart = S.read_json("live_state_smart.json") or {}
    advisories = S.read_json(ADVISORIES, []) or []
    blocked = {a["road"] for a in advisories if a.get("closed") and a.get("road")}
    blocked.update(reverse for road in tuple(blocked) if (reverse := net.reverse_of(road)))
    live = {e: r["tt"] for e, r in smart.get("roads", {}).items()}
    free = {e: d["length"] / max(d["speed"], 0.1) for e, d in net.edges.items()}
    best, t_best = net.best_route(a, b, live, blocked)
    if not best:
        return {"route": None, "closed_roads": sorted(blocked)}
    usual, _ = net.best_route(a, b, free, blocked)
    t_usual = sum(live.get(e, free[e]) for e in usual) if usual else None
    return {"route": best, "t_best": round(t_best, 1), "usual": usual,
            "t_usual": round(t_usual, 1) if t_usual else None}


def send_command(cmd):
    cmds = S.read_json(S.COMMANDS, []) or []
    cmd["id"] = max([c.get("id", 0) for c in cmds], default=0) + 1
    cmd["sent"] = time.time()
    S.write_json(S.COMMANDS, (cmds + [cmd])[-200:])


def update_demo(changes):
    c = dict(S.read_json(S.DEMO_FILE, {}) or {})
    c.update(changes)
    S.write_json(S.DEMO_FILE, c)
    return c


def advisory_now():
    return dt.datetime.now(dt.timezone(dt.timedelta(hours=5, minutes=30)))


def isoformat_ist(value):
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        d = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone(dt.timedelta(hours=5, minutes=30)))
    return d.astimezone(dt.timezone(dt.timedelta(hours=5, minutes=30))).isoformat()


def advisory_state(advisory, now=None):
    if not isinstance(advisory, dict):
        return "draft"
    status = str(advisory.get("status") or "draft").lower()
    if status == "withdrawn":
        return "withdrawn"
    if status == "draft":
        return "draft"
    start = isoformat_ist(advisory.get("start"))
    end = isoformat_ist(advisory.get("end"))
    if start is None:
        return "draft"
    now = advisory_now() if now is None else now
    start_dt = dt.datetime.fromisoformat(start)
    end_dt = dt.datetime.fromisoformat(end) if end else None
    if now < start_dt:
        return "upcoming"
    if end_dt is None or now < end_dt:
        return "active"
    return "expired"


def advisory_sort_key(item):
    now = advisory_now()
    start_raw = item.get("start")
    start = dt.datetime.fromisoformat(start_raw) if start_raw else now
    state = advisory_state(item, now)
    if state == "active":
        sev = 0 if str(item.get("severity") or "info").lower() == "critical" else 1
        return (0, sev, start)
    if state == "upcoming":
        return (1, start)
    if state == "expired":
        return (2, start)
    if state == "withdrawn":
        return (3, start)
    return (4, start)


def normalize_advisory(raw, created_by="system"):
    if not isinstance(raw, dict):
        raise ValueError("Expected an advisory object")
    kind = str(raw.get("type") or "").strip()
    if kind not in ADVISORY_TYPES:
        raise ValueError("Choose a valid advisory type")
    title = str(raw.get("title") or "").strip()
    if len(title) < 5 or len(title) > 80:
        raise ValueError("Title must be 5 to 80 characters")
    description = str(raw.get("description") or raw.get("details") or "").strip()
    if len(description) > 600:
        raise ValueError("Description must be 600 characters or fewer")
    road = str(raw.get("road_id") or raw.get("road") or "").strip()
    if road and road not in net.edges:
        raise ValueError("Road ID is not in the network")
    start = isoformat_ist(raw.get("start"))
    if not start:
        raise ValueError("Start time is required")
    start_dt = dt.datetime.fromisoformat(start)
    end = isoformat_ist(raw.get("end")) if raw.get("end") not in (None, "") else None
    if end is not None:
        end_dt = dt.datetime.fromisoformat(end)
        if end_dt <= start_dt:
            raise ValueError("End time must be later than the start time")
        if end_dt - start_dt > dt.timedelta(days=30):
            raise ValueError("Advisory duration must be 30 days or less")
    severity = str(raw.get("severity") or "info").lower()
    if severity not in ADVISORY_SEVERITY:
        severity = "info"
    closure = str(raw.get("closure") or "info").lower()
    if closure not in ADVISORY_CLOSURE:
        closure = "info"
    status = str(raw.get("status") or "draft").lower()
    if status not in {"draft", "published", "withdrawn"}:
        status = "draft"
    if status == "withdrawn":
        if not str(raw.get("withdraw_reason") or "").strip():
            raise ValueError("Withdraw reason is required")
    advisory_id = raw.get("id")
    try:
        advisory_id = int(advisory_id) if advisory_id is not None else None
    except (TypeError, ValueError):
        raise ValueError("Invalid advisory id")
    now = advisory_now().isoformat()
    item = {
        "id": advisory_id,
        "type": kind,
        "title": title,
        "road_id": road,
        "location_text": str(raw.get("location_text") or raw.get("location") or "").strip(),
        "closure": closure,
        "severity": severity,
        "start": start,
        "end": end,
        "alternative_route": str(raw.get("alternative_route") or raw.get("alternative") or "").strip(),
        "description": description,
        "helpline": str(raw.get("helpline") or "").strip() or None,
        "status": status,
        "created_by": str(raw.get("created_by") or created_by).strip() or "system",
        "created_at": str(raw.get("created_at") or now),
        "updated_at": str(raw.get("updated_at") or now),
        "published_at": isoformat_ist(raw.get("published_at")) if raw.get("published_at") else (now if status == "published" else None),
        "withdrawn_at": isoformat_ist(raw.get("withdrawn_at")) if raw.get("withdrawn_at") else None,
        "withdraw_reason": str(raw.get("withdraw_reason") or "").strip() or None,
        "history": list(raw.get("history") or []),
    }
    if not item["history"]:
        item["history"] = [{"time": now, "user": item["created_by"], "action": "created"}]
    return item


def load_advisories():
    return S.read_json(ADVISORIES, []) or []


def save_advisories(advisories):
    path = os.path.join(S.HERE, ADVISORIES)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(advisories, f)
    os.replace(tmp, path)


def advisory_payload(item, now=None):
    item = dict(item)
    now = advisory_now() if now is None else now
    item["state"] = advisory_state(item, now)
    if item["state"] == "upcoming":
        start_dt = dt.datetime.fromisoformat(isoformat_ist(item.get("start")))
        item["starts_in_min"] = max(0, int((start_dt - now).total_seconds() // 60))
        item["minutes_left"] = None
    elif item["state"] == "active":
        item["starts_in_min"] = None
        end_raw = item.get("end")
        if end_raw:
            end_dt = dt.datetime.fromisoformat(isoformat_ist(end_raw))
            item["minutes_left"] = max(0, int((end_dt - now).total_seconds() // 60))
        else:
            item["minutes_left"] = None
    else:
        item["starts_in_min"] = None
        item["minutes_left"] = None
    return item


def public_advisories(all_mode=False):
    now = advisory_now()
    rows = []
    for item in load_advisories():
        item = dict(item)
        state = advisory_state(item, now)
        if all_mode:
            rows.append(advisory_payload(item, now))
            continue
        if item.get("status") != "published":
            continue
        if state == "upcoming":
            rows.append(advisory_payload(item, now))
        elif state == "active":
            rows.append(advisory_payload(item, now))
        elif state == "expired":
            end_raw = item.get("end")
            if end_raw:
                end_dt = dt.datetime.fromisoformat(isoformat_ist(end_raw))
                if now - end_dt <= dt.timedelta(hours=24):
                    rows.append(advisory_payload(item, now))
    rows.sort(key=advisory_sort_key)
    return rows


def log_advisory(user, advisory_id, action, title):
    path = os.path.join(S.HERE, "advisories_log.csv")
    make_head = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if make_head:
            w.writerow(["time", "user", "id", "action", "title"])
        w.writerow([advisory_now().isoformat(), user or "system", advisory_id, action, title])


def update_advisories(change, user_name="system"):
    if not isinstance(change, dict):
        raise ValueError("Expected an advisory object")
    advisories = load_advisories()
    action = change.get("action", "save")
    if action == "delete":
        try:
            advisory_id = int(change.get("id"))
        except (TypeError, ValueError):
            raise ValueError("Invalid advisory id")
        updated = [a for a in advisories if int(a.get("id", -1)) != advisory_id]
        if len(updated) == len(advisories):
            raise ValueError("Advisory not found")
        save_advisories(updated)
        log_advisory(user_name, advisory_id, "deleted", str(change.get("title") or ""))
        return updated

    advisory = normalize_advisory(change, user_name)
    if advisory.get("id") is None:
        advisory["id"] = max((int(a.get("id", 0)) for a in advisories), default=0) + 1
        advisory["created_by"] = user_name or advisory.get("created_by") or "system"
        advisory["created_at"] = advisory_now().isoformat()
        advisory["updated_at"] = advisory["created_at"]
        if advisory.get("status") == "published":
            advisory["published_at"] = advisory["created_at"]
        if advisory.get("status") == "withdrawn":
            advisory["withdrawn_at"] = advisory["created_at"]
        advisory["history"] = [{"time": advisory["created_at"], "user": advisory["created_by"], "action": "created"}]
        advisories.insert(0, advisory)
        log_advisory(user_name, advisory["id"], "created", advisory.get("title", ""))
    else:
        for idx, existing in enumerate(advisories):
            if int(existing.get("id", -1)) == advisory["id"]:
                old = dict(existing)
                advisory["created_by"] = old.get("created_by") or user_name or "system"
                advisory["created_at"] = old.get("created_at") or advisory_now().isoformat()
                advisory["updated_at"] = advisory_now().isoformat()
                advisory["published_at"] = old.get("published_at")
                advisory["withdrawn_at"] = old.get("withdrawn_at")
                advisory["withdraw_reason"] = old.get("withdraw_reason")
                if advisory.get("status") == "published" and old.get("status") != "published":
                    advisory["published_at"] = advisory["updated_at"]
                if advisory.get("status") == "withdrawn":
                    advisory["withdrawn_at"] = advisory["updated_at"]
                    advisory["withdraw_reason"] = advisory.get("withdraw_reason") or old.get("withdraw_reason") or "withdrawn"
                history = list(old.get("history") or [])
                history.append({"time": advisory["updated_at"], "user": user_name or advisory["created_by"], "action": "edited"})
                advisory["history"] = history
                advisories[idx] = advisory
                log_advisory(user_name, advisory["id"], "edited", advisory.get("title", ""))
                break
        else:
            raise ValueError("Advisory not found")
    save_advisories(advisories)
    return advisories


# ---------------------------------------------------------------- web server
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):          # keep the terminal quiet
        pass

    def cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Control-Key")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def do_OPTIONS(self):                      # browser check before a POST from Netlify
        self.send_response(204)
        self.cors()
        self.end_headers()

    def send(self, body, ctype="application/json", code=200, headers=None):
        if isinstance(body, (dict, list)) or body is None:
            body = json.dumps(body).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        response_headers = headers or {}
        if not any(name.lower() == "cache-control" for name in response_headers):
            self.send_header("Cache-Control", "no-store")
        self.cors()
        for name, value in response_headers.items():
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def file(self, path, ctype, headers=None):
        try:
            with open(path, "rb") as f:
                self.send(f.read(), ctype, headers=headers)
        except OSError:
            if ctype.startswith("text/html"):
                self.send(f"<h2>Page file missing</h2><p>Could not find <b>{path}</b>.</p>"
                          "<p>Copy the <b>web</b> folder from the zip into the same folder as "
                          "dashboard_server.py, then restart it.</p>", "text/html; charset=utf-8", 404)
            else:
                self.send({"error": f"missing file {os.path.basename(path)}"}, code=404)

    def redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_GET(self):
        cam_path = urlparse(self.path).path.rstrip("/")
        if cam_path.startswith("/api/camera/") or cam_path == "/camera":      # live YOLO camera: authority only
            if not session_user(self):
                if cam_path == "/camera":
                    return self.redirect("/login")
                return self.send({"error": "login required"}, code=401)
            if camera_web.handle(self):
                return
        u = urlparse(self.path)
        q = {k: v[0] for k, v in parse_qs(u.query).items()}
        p = u.path.rstrip("/") or "/"
        if p == "/":
            self.file(os.path.join(WEB, "index.html"), "text/html; charset=utf-8")
        elif p == "/login":
            self.file(os.path.join(WEB, "login.html"), "text/html; charset=utf-8")
        elif p == "/control":
            if session_user(self):
                self.file(os.path.join(WEB, "operator.html"), "text/html; charset=utf-8")
            else:
                self.redirect("/login")
        elif p == "/public":
            self.file(os.path.join(WEB, "public.html"), "text/html; charset=utf-8")
        elif p.startswith("/media/"):
            name = p[len("/media/"):]
            media_dir = os.path.join(WEB, "media")
            extension_types = {
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".png": "image/png",
                ".webp": "image/webp",
            }
            extension = os.path.splitext(name)[1].lower()
            if (not name or name in (".", "..") or ".." in name or os.path.splitdrive(name)[0] or
                    "/" in name or "\\" in name or os.path.basename(name) != name or
                    extension not in extension_types):
                self.send({"error": "not found"}, code=404)
            else:
                self.file(os.path.join(media_dir, name), extension_types[extension],
                          headers={"Cache-Control": "public, max-age=86400"})
        elif p in ("/common.css", "/common.js", "/config.js"):
            ctype = "text/css; charset=utf-8" if p.endswith(".css") else "text/javascript; charset=utf-8"
            self.file(os.path.join(WEB, p[1:]), ctype)
        elif p == "/api/me":
            user = session_user(self)
            self.send({"user": user} if user else {"error": "login required"}, code=200 if user else 401)
        elif p == "/api/net":
            self.send(net_json())
        elif p == "/api/state":
            self.send(state())
        elif p == "/api/timeline":
            self.send({"fixed": timeline("fixed"), "smart": timeline("smart")})
        elif p == "/api/forecast":
            self.send(S.read_json("forecast.json"))
        elif p == "/api/route":
            self.send(route(q.get("from"), q.get("to")))
        elif p == "/api/advisories":
            if q.get("all") == "1":
                if not session_user(self):
                    self.send({"error": "login required"}, code=401)
                    return
                self.send({"advisories": public_advisories(all_mode=True)})
            else:
                self.send({"advisories": public_advisories()})
        elif p == "/api/camera/stream":
            if not session_user(self):
                self.send({"error": "login required"}, code=401)
                return
            self.camera_stream()
        elif p == "/api/camera/snapshot.jpg":
            if not session_user(self):
                self.send({"error": "login required"}, code=401)
                return
            path = os.path.join(S.HERE, "camera_frame.jpg")
            try:
                with open(path, "rb") as f:
                    image = f.read()
            except OSError:
                self.send({"error": "camera frame unavailable"}, code=404)
                return
            if not image:
                self.send({"error": "camera frame unavailable"}, code=404)
                return
            self.send(image, "image/jpeg", headers={"Content-Disposition": "attachment; filename=camera_snapshot.jpg"})
        elif p == "/api/camera/stats":
            if not session_user(self):
                self.send({"error": "login required"}, code=401)
                return
            self.send(read_camera_stats())
        elif p == "/comparison.png":
            self.file("comparison.png", "image/png")
        elif p == "/camera-frame.jpg":
            self.file("camera_frame.jpg", "image/jpeg")
        elif p.startswith("/img/") and p[5:] in ("school.png", "office.png", "hospital.png"):
            self.file(p[5:], "image/png")
        else:
            self.send("<h2>Unknown address</h2><p>Open <a href='/'>the control room</a> "
                      "or <a href='/public'>the public page</a>.</p>", "text/html; charset=utf-8", 404)

    def camera_stream(self):
        path = os.path.join(S.HERE, "camera_frame.jpg")
        boundary = b"frame"
        self.send_response(200)
        self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        last_mtime = None
        last_frame_at = time.monotonic()
        try:
            last_frame_at -= max(0, time.time() - os.path.getmtime(path))
        except OSError:
            pass
        while time.monotonic() - last_frame_at <= 30:
            try:
                mtime = os.stat(path).st_mtime_ns
                if mtime != last_mtime:
                    with open(path, "rb") as f:
                        image = f.read()
                    if image:
                        frame = (b"--" + boundary + b"\r\n"
                                 b"Content-Type: image/jpeg\r\n"
                                 b"Content-Length: " + str(len(image)).encode("ascii") + b"\r\n\r\n"
                                 + image + b"\r\n")
                        self.wfile.write(frame)
                        self.wfile.flush()
                        last_mtime = mtime
                        last_frame_at = time.monotonic()
                readable, _, _ = select.select([self.connection], [], [], 0)
                if readable and not self.connection.recv(1, socket.MSG_PEEK):
                    return
            except FileNotFoundError:
                pass
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            except OSError:
                pass
            time.sleep(0.05)

    def do_POST(self):
        p = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0) or 0)
        if p == "/api/camera-frame":
            if n <= 0 or n > 5_000_000:
                return self.send({"error": "invalid frame size"}, code=400)
            jpeg = self.rfile.read(n)
            if not jpeg.startswith(b"\xff\xd8") or not jpeg.endswith(b"\xff\xd9"):
                return self.send({"error": "expected a JPEG frame"}, code=400)
            path = os.path.join(S.HERE, "camera_frame.jpg")
            tmp = f"{path}.{os.getpid()}.tmp"
            try:
                with open(tmp, "wb") as f:
                    f.write(jpeg)
                os.replace(tmp, path)
            except OSError:
                return self.send({"error": "could not store camera frame"}, code=500)
            return self.send({"ok": True})
        if p in ("/api/camera/frame", "/api/camera/stats"):
            if not camera_ingest_allowed(self):
                return self.send({"error": "login required"}, code=401)
            max_size = 2 * 1024 * 1024 if p.endswith("/frame") else 64 * 1024
            if n <= 0 or n > max_size:
                return self.send({"error": "invalid camera payload size"}, code=400)
            data = self.rfile.read(n)
            if p.endswith("/frame"):
                if not data.startswith(b"\xff\xd8") or not data.endswith(b"\xff\xd9"):
                    return self.send({"error": "expected a JPEG frame"}, code=400)
                target = "camera_frame.jpg"
            else:
                try:
                    stats = json.loads(data)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    return self.send({"error": "bad camera stats JSON"}, code=400)
                if not isinstance(stats, dict) or not isinstance(stats.get("time"), (int, float)):
                    return self.send({"error": "camera stats must be an object with an epoch time"}, code=400)
                target = "camera_stats.json"
                data = json.dumps(stats).encode("utf-8")
            if not save_camera_file(target, data):
                return self.send({"error": "could not store camera data"}, code=500)
            return self.send({"ok": True})
        try:
            body = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return self.send({"error": "bad json"}, code=400)
        if p == "/api/login":
            ip = self.client_address[0]
            now = time.time()
            with _AUTH_LOCK:
                failures, blocked_until = _LOGIN_ATTEMPTS.get(ip, (0, 0))
                if blocked_until > now:
                    return self.send({"error": "too many login attempts"}, code=429)
                if blocked_until:
                    failures, blocked_until = 0, 0
                    _LOGIN_ATTEMPTS.pop(ip, None)
            user = body.get("user") if isinstance(body, dict) else None
            password = body.get("password") if isinstance(body, dict) else None
            if not isinstance(user, str) or not isinstance(password, str):
                user, password = "", ""
            stored = S.AUTH_USERS.get(user)
            valid = verify_password(password, stored if isinstance(stored, str) else None)
            if not valid or stored is None:
                with _AUTH_LOCK:
                    failures, _ = _LOGIN_ATTEMPTS.get(ip, (0, 0))
                    failures += 1
                    _LOGIN_ATTEMPTS[ip] = (failures, time.time() + 300 if failures >= 5 else 0)
                return self.send({"error": "Invalid user ID or password"}, code=401)
            with _AUTH_LOCK:
                _LOGIN_ATTEMPTS.pop(ip, None)
            token = make_session(user)
            return self.send({"ok": True}, headers={
                "Set-Cookie": f"session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={_SESSION_TTL}"
            })
        if p == "/api/logout":
            cookie = SimpleCookie()
            try:
                cookie.load(self.headers.get("Cookie", ""))
                token = cookie["session"].value
            except (KeyError, CookieError):
                token = None
            if token:
                with _AUTH_LOCK:
                    _SESSIONS.pop(token, None)
            return self.send({"ok": True}, headers={
                "Set-Cookie": "session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
            })
        if p == "/api/advisories" and S.CONTROL_KEY and self.headers.get("X-Control-Key") != S.CONTROL_KEY:
            return self.send({"error": "control locked"}, code=403)
        if p.startswith("/api/advisories"):
            user = authority_user(self)
            if not user:
                return self.send({"error": "login required"}, code=401)
            if p == "/api/advisories":
                try:
                    self.send({"advisories": update_advisories(body, user)})
                except ValueError as e:
                    self.send({"error": str(e)}, code=400)
                return
            if p.endswith("/publish"):
                try:
                    adv_id = int(p.rsplit("/", 1)[0].rsplit("/", 1)[-1])
                except ValueError:
                    return self.send({"error": "invalid advisory id"}, code=400)
                advisories = load_advisories()
                for idx, item in enumerate(advisories):
                    if int(item.get("id", -1)) == adv_id:
                        item = dict(item)
                        item["status"] = "published"
                        item["published_at"] = advisory_now().isoformat()
                        item["updated_at"] = item["published_at"]
                        history = list(item.get("history") or [])
                        history.append({"time": item["published_at"], "user": user, "action": "published"})
                        item["history"] = history
                        advisories[idx] = item
                        save_advisories(advisories)
                        log_advisory(user, adv_id, "published", item.get("title", ""))
                        self.send({"ok": True, "advisory": advisory_payload(item)})
                        return
                return self.send({"error": "advisory not found"}, code=404)
            if p.endswith("/withdraw"):
                reason = str((body or {}).get("reason") or "").strip()
                if not reason:
                    return self.send({"error": "withdraw reason is required"}, code=400)
                adv_id = int(p.rsplit("/", 1)[0].rsplit("/", 1)[-1])
                advisories = load_advisories()
                for idx, item in enumerate(advisories):
                    if int(item.get("id", -1)) == adv_id:
                        item = dict(item)
                        item["status"] = "withdrawn"
                        item["withdrawn_at"] = advisory_now().isoformat()
                        item["withdraw_reason"] = reason
                        item["updated_at"] = item["withdrawn_at"]
                        history = list(item.get("history") or [])
                        history.append({"time": item["withdrawn_at"], "user": user, "action": "withdrawn"})
                        item["history"] = history
                        advisories[idx] = item
                        save_advisories(advisories)
                        log_advisory(user, adv_id, "withdrawn", item.get("title", ""))
                        self.send({"ok": True, "advisory": advisory_payload(item)})
                        return
                return self.send({"error": "advisory not found"}, code=404)
            if p.count("/") >= 3:
                adv_id = int(p.split("/")[-1])
                advisories = load_advisories()
                for idx, item in enumerate(advisories):
                    if int(item.get("id", -1)) == adv_id:
                        try:
                            updated = normalize_advisory({**item, **(body or {}), "id": adv_id}, user)
                        except ValueError as e:
                            return self.send({"error": str(e)}, code=400)
                        updated["updated_at"] = advisory_now().isoformat()
                        history = list(updated.get("history") or [])
                        history.append({"time": updated["updated_at"], "user": user, "action": "edited"})
                        updated["history"] = history
                        advisories[idx] = updated
                        save_advisories(advisories)
                        log_advisory(user, adv_id, "edited", updated.get("title", ""))
                        self.send({"ok": True, "advisory": advisory_payload(updated)})
                        return
                return self.send({"error": "advisory not found"}, code=404)
        if p == "/api/command":
            if not session_user(self) and not (S.CONTROL_KEY and
                    hmac.compare_digest(self.headers.get("X-Control-Key", ""), S.CONTROL_KEY)):
                return self.send({"error": "login required"}, code=401)
            send_command(body)
            self.send({"ok": True})
        elif p == "/api/demo":
            if not session_user(self) and not (S.CONTROL_KEY and
                    hmac.compare_digest(self.headers.get("X-Control-Key", ""), S.CONTROL_KEY)):
                return self.send({"error": "login required"}, code=401)
            self.send(update_demo(body))
        elif p == "/api/yolo":                 # camera counts from YOLO on another laptop
            body.setdefault("time", time.time())
            S.write_json(S.YOLO_FILE, body)
            self.send({"ok": True})
        else:
            self.send({"error": "not found"}, code=404)


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "<laptop-IP>"


if __name__ == "__main__":
    missing = [f for f in ("index.html", "login.html", "operator.html", "public.html", "common.js", "common.css")
               if not os.path.exists(os.path.join(WEB, f))]
    if missing:
        print(f"WARNING: missing in {WEB}: {', '.join(missing)}")
        print("Copy the 'web' folder from the zip next to dashboard_server.py.\n")
    class DashboardHTTPServer(ThreadingHTTPServer):
        daemon_threads = True

    srv = DashboardHTTPServer(("0.0.0.0", PORT), Handler)
    ip = lan_ip()
    print("Dashboards running (press Ctrl+C to stop):")
    print(f"  Start page   : http://localhost:{PORT}")
    print(f"  Authority    : http://localhost:{PORT}/login")
    print(f"  Control room : http://localhost:{PORT}/control")
    print(f"  Public       : http://localhost:{PORT}/public")
    print(f"  Phone        : http://{ip}:{PORT}/public   (same Wi-Fi)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
