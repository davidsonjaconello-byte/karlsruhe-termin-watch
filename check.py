"""Check Karlsruhe Buergerbuero for free 'Wohnsitz - Zuzug nach Karlsruhe' slots on a target date.

Prints any free slots and, if NTFY_TOPIC is set, pushes them to the ntfy app.
Exit code 0 = found slots, 1 = none, 2 = error.
"""
import json, os, sys, time, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

BASE = "https://karlsruhe.konsentas.de/api/"
PROCESS = "34"  # Wohnsitz - Zuzug nach Karlsruhe
TARGET = os.environ.get("TARGET_DATE", "2026-11-02")
TOPIC = os.environ.get("NTFY_TOPIC", "")
BOOK_URL = "https://karlsruhe.konsentas.de/form/28/?signup_new=1"


def call(method, path, token=None, data=None):
    req = urllib.request.Request(BASE + path, method=method)
    req.add_header("User-Agent", "Mozilla/5.0")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data).encode()
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
    with urllib.request.urlopen(req, body, timeout=30) as r:
        return json.load(r)


def find_slots():
    start = call("GET", "getOtaStartUp/?signupform_id=28&userauth=&queryParameter%5Bsignup_new%5D=1&r=")["data"]
    token = start["ota_jwt"]
    option = None

    def walk(o):
        nonlocal option
        if isinstance(o, dict):
            if o.get("recno") == PROCESS and "options" in o:
                option = next(x["recno"] for x in o["options"] if x["option_name"] == "1 Vorgang")
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(start["op_conf"])
    step = call("POST", "postOtaNextStep/", token, {
        "formdata[processes][]": "opt-" + option,
        "op_id": start["op_id"],
        "navigation": "1",
    })
    if step.get("code") != 3:
        raise RuntimeError("service step failed: %s" % json.dumps(step)[:300])
    token = step["data"].get("ota_jwt", token)

    day = datetime.strptime(TARGET, "%Y-%m-%d").replace(tzinfo=timezone(timedelta(hours=1)))
    s = int(day.timestamp())
    res = call("GET", "brick_ota_termin_getTimeslot/?start=%d&end=%d" % (s, s + 86400), token)
    if res.get("code") != 3:
        raise RuntimeError("timeslot call failed: %s" % json.dumps(res)[:300])
    want = int(TARGET.replace("-", ""))
    slots = []
    for t in res["data"]["termins"]:
        if t.get("yeardate") == want and t.get("places", 0) > 0 and t.get("time") != "":
            place = next(iter(t["conditions"].values()))["name"]
            slots.append("%02d:%02d %s" % (int(t["time"]) // 60, int(t["time"]) % 60, place))
    return sorted(set(slots))


def push(title, msg, priority):
    if not TOPIC:
        return
    req = urllib.request.Request("https://ntfy.sh/" + TOPIC, data=msg.encode(), method="POST")
    req.add_header("Title", title)
    req.add_header("Priority", priority)
    req.add_header("Click", BOOK_URL)
    urllib.request.urlopen(req, timeout=30)


def notify(slots):
    msg = "Free on %s:\n%s\nBook now!" % (TARGET, "\n".join(slots[:10]))
    push("Karlsruhe Anmeldung slot free!", msg, "urgent")


def watch(minutes, every=60):
    """Check every `every` seconds for `minutes`; push only when new slots show up."""
    stop = time.time() + minutes * 60
    seen = set()
    errors = 0
    first = True
    while time.time() < stop:
        if datetime.now(timezone(timedelta(hours=1))).strftime("%Y-%m-%d") > TARGET:
            print("target date has passed, stopping")
            return
        try:
            slots = find_slots()
            print(time.strftime("%H:%M:%S"), slots or "nothing free", flush=True)
            new = [x for x in slots if x not in seen]
            if new:
                notify(new)
            seen = set(slots)
            errors = 0
            if first and not slots:
                push("Watcher running", "Checking %s every minute. Nothing free right now." % TARGET, "low")
        except Exception as e:
            errors += 1
            print(time.strftime("%H:%M:%S"), "ERROR", e, flush=True)
            if errors == 5:
                push("Watcher can't read the booking site", "5 failed checks in a row: %s" % e, "high")
        first = False
        time.sleep(every)


if __name__ == "__main__":
    if len(sys.argv) > 1:
        watch(float(sys.argv[1]))
        sys.exit(0)
    try:
        slots = find_slots()
    except Exception as e:
        print("ERROR", e)
        sys.exit(2)
    print(time.strftime("%H:%M:%S"), TARGET, slots or "nothing free")
    if slots:
        notify(slots)
        sys.exit(0)
    sys.exit(1)
