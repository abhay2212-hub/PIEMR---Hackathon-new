# Linking YOLO to the simulation

## Your teammate's program (INDORE / TrafficJamAI) - ready to use
`main_linked.py` is his main.py (phone CCTV + YOLO11n + TRAFFIC ALERT) with the link added.
`yolo11n.pt` (his model file) is already in this folder.
```
pip install ultralytics opencv-python lap
python main_linked.py                              # his phone stream (edit PHONE_STREAM at the top)
python main_linked.py --source video.mp4           # or a video file
```
It counts each vehicle once (tracking) and sends the counts for road W every 10 s.
In settings.py:  CAMERA_JUNCTION = "J1",  CAMERA_ROADS = {"W": "E0"},  YOLO_EVERY = 10

---------------------------------------------------------------------------------------------------

The simulation (`both.py --yolo`) reads **yolo_counts.json** every 30 s: how many vehicles came from
each road (N, E, S, W) of the junction in the video, their types, and how many are waiting.
Pick ONE of the three ways below.

---------------------------------------------------------------------------------------------------
## Option A - his YOLO only detects vehicles  ->  use camera_yolo.py (ready-made, easiest)

```
pip install ultralytics opencv-python lap
python camera_yolo.py --source junction.mp4 --draw                      # once: draw the 4 roads
python camera_yolo.py --source junction.mp4 --model his_model.pt        # run
```
In the drawing window: press 1/2/3/4 for road N/E/S/W, click 3+ points around the WAITING AREA of
that road, press `l` and click 2 points for the COUNT LINE (stop line), repeat for every road, press `s`.
`--model` = his trained .pt file (without it, the standard yolov8n.pt is used).
Phone camera instead of a file: `--source http://<phone-IP>:4747/video` (DroidCam / IP Webcam).

---------------------------------------------------------------------------------------------------
## Option B - his YOLO already counts per road  ->  add 3 lines to his code

Copy **yolo_link.py** next to his program, then:
```python
from yolo_link import CameraLink
link = CameraLink(folder=r"C:\path\to\traffic_system")       # same laptop
# link = CameraLink(server="http://192.168.1.5:8600")         # other laptop, same Wi-Fi

# in his loop, every frame:
link.vehicle_passed("N", class_name)   # when a vehicle from road N crosses the line (once per vehicle)
link.queue("N", waiting_on_N)          # vehicles waiting on road N now (do this for every road)
link.tick()                            # sends every 30 s by itself
```
Class names like car, motorcycle, autorickshaw, bus, truck are understood automatically.

---------------------------------------------------------------------------------------------------
## Option C - write the file himself
Every 30 s overwrite `yolo_counts.json` in the traffic_system folder:
```json
{"time": 1696830000.0, "window_s": 30,
 "roads": {"N": {"count": 12, "queue": 5, "car": 5, "bike": 4, "auto": 1, "bus": 1, "truck": 1},
           "E": {...}, "S": {...}, "W": {...}}}
```
(or POST the same JSON to `http://<simulation-laptop-IP>:8600/api/yolo`)

---------------------------------------------------------------------------------------------------
## Run everything
1. Terminal 1: `python dashboard_server.py`
2. Terminal 2: the YOLO program (Option A or his own)
3. Terminal 3: `python both.py --yolo --predict`

The terminal and dashboard show `CAMERA: counts N:12, E:7, ...` every 30 s, the "Camera" panel shows
the counts, and SUMO adds the same vehicles on the matching roads.

Which SUMO road is N/E/S/W: printed at start as `Camera junction: J2  roads: {...}`.
If it is wrong, set `CAMERA_JUNCTION` and `CAMERA_ROADS` in settings.py, e.g.
`CAMERA_ROADS = {"N": "-E3", "E": "-E4", "S": "E7", "W": "E1"}` (roads coming INTO the junction).

No camera yet? `python fake_yolo.py` pretends to be YOLO.
