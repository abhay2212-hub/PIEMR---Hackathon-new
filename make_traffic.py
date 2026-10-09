"""Create the traffic for your netedit network: cars, bikes, autos, buses and trucks.

    python make_traffic.py      -> vtypes.xml + traffic.rou.xml

- normal traffic between all entry and exit roads
- one rush hour on the first entry road
- extra traffic to/from the school and office during their rush
  (predicted times from forecast.json if you ran predict.py, otherwise the times in settings.py)
"""
import settings as S

# ================= TRAFFIC SETTINGS =================
VEH_PER_HOUR = 700         # vehicles per hour from EACH entry road
MIX = {"car": 0.40, "bike": 0.35, "auto": 0.10, "bus": 0.05, "truck": 0.10}
RUSH = {"from": None, "begin": 600, "end": 1800, "veh_per_hour": 900}   # from=None -> first entry
# ====================================================

VTYPES = """<routes>
    <vType id="car" vClass="passenger" length="4.5" maxSpeed="16.7" guiShape="passenger" color="60,130,255"/>
    <vType id="bike" vClass="motorcycle" length="2.0" maxSpeed="16.7" guiShape="motorcycle" color="255,210,0"/>
    <vType id="auto" vClass="passenger" length="3.2" maxSpeed="12.5" guiShape="passenger/hatchback" color="40,190,80"/>
    <vType id="bus" vClass="bus" length="11.0" maxSpeed="13.9" guiShape="bus" color="240,120,30"/>
    <vType id="truck" vClass="truck" length="8.0" maxSpeed="13.9" guiShape="truck" color="140,90,50"/>
</routes>
"""


def flow(fid, vt, begin, end, rate, a, b):
    return (begin, f'    <flow id="{fid}" type="{vt}" begin="{begin}" end="{end}" vehsPerHour="{rate:.1f}" '
                   f'from="{a}" to="{b}" departLane="best" departSpeed="max"/>')


def main():
    net = S.Net(S.NET)
    if not net.entries or not net.exits:
        raise SystemExit("No entry/exit roads found. Your network needs outer end points with roads in and out.")
    print("Entry roads:", net.entries)
    print("Exit roads: ", net.exits)
    items, n = [], 0

    for en in net.entries:
        dests = [x for x in net.exits if net.edges[x]["to"] != net.edges[en]["from"]]
        for ex in dests:
            for vt, share in MIX.items():
                rate = VEH_PER_HOUR / len(dests) * share
                if rate >= 1:
                    items.append(flow(f"f{n}_{vt}", vt, 0, S.SIM_TIME, rate, en, ex))
                    n += 1

    rush_from = RUSH["from"] if RUSH["from"] in net.entries else net.entries[0]
    dests = [x for x in net.exits if net.edges[x]["to"] != net.edges[rush_from]["from"]]
    if dests and RUSH["veh_per_hour"]:
        far = dests[len(dests) // 2]
        for vt, share in MIX.items():
            if RUSH["veh_per_hour"] * share >= 1:
                items.append(flow(f"rush_{vt}", vt, RUSH["begin"], RUSH["end"], RUSH["veh_per_hour"] * share,
                                  rush_from, far))
        print(f"Rush: {rush_from} -> {far}, {RUSH['veh_per_hour']} veh/h, {RUSH['begin']}-{RUSH['end']} s")

    # school / office rush (only for places that are not busy all day)
    places = S.pick_place_edges(net, [dict(p) for p in S.PLACES])
    fc = S.read_json("forecast.json")
    start = S.hhmm(S.START_CLOCK)
    for p in places:
        wins = S.predicted_windows(fc, p["name"]) or p["times"]
        for a, b in wins:
            if S.hhmm(b) - S.hhmm(a) > 6 * 3600:
                continue
            begin, end = max(0, S.hhmm(a) - start), min(S.SIM_TIME, S.hhmm(b) - start)
            if end <= begin:
                continue
            per = S.PLACE_SURGE / 2 / len(net.entries)
            for k, en in enumerate(net.entries):
                ex = net.exits[k % len(net.exits)]
                for vt, share in MIX.items():
                    if per * share >= 1:
                        items.append(flow(f"{p['name']}_{a.replace(':', '')}_{k}_in_{vt}", vt, begin, end, per * share, en, p["edge"]))
                        items.append(flow(f"{p['name']}_{a.replace(':', '')}_{k}_out_{vt}", vt, begin, end, per * share,
                                          p["edge"], ex))
            print(f"{p['name']} rush on {p['edge']}: {a}-{b} -> sim {begin}-{end} s, +{S.PLACE_SURGE} veh/h"
                  + ("  (predicted)" if fc else ""))

    items.sort(key=lambda x: x[0])
    with open(S.VTYPES, "w") as f:
        f.write(VTYPES)
    with open(S.ROUTES, "w") as f:
        f.write("<routes>\n" + "".join(line + "\n" for _, line in items) + "</routes>\n")
    print(f"Wrote {S.VTYPES} and {S.ROUTES} ({len(items)} flows). Simulation starts at {S.START_CLOCK}.")


if __name__ == "__main__":
    main()
