# Smart AndonLedger AI
# Arduino UNO Q + App Lab + Edge Impulse FOMO object detection
#
# Expected Edge Impulse classes: red, yellow, green
#
# Important transition rule:
# The code processes the SET of active detections in each frame.
# Example:
#   {"red"}          -> FAULT
#   {"green"}        -> RUNNING
#   {"red","green"}  -> FAULT (prototype priority: RED > YELLOW > GREEN)
# When red disappears and green remains stable, the state changes to RUNNING.
#
# Adjust derive_machine_state() to match the target machine's documented
# stack-light convention.

from arduino.app_utils import App, Logger
from arduino.app_bricks.web_ui import WebUI
from arduino.app_bricks.video_objectdetection import VideoObjectDetection

from collections import deque
from datetime import datetime, timezone
from pathlib import Path
import csv
import threading
import time


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

INITIAL_CONFIDENCE_THRESHOLD = 0.60
STABLE_SECONDS = 1.0
NO_SIGNAL_TIMEOUT_SECONDS = 2.0
TELEMETRY_PERIOD_SECONDS = 1.0
LONG_STOP_SECONDS = 60.0
RECENT_EVENT_LIMIT = 12

VALID_LABELS = {"red", "yellow", "green"}


# ---------------------------------------------------------------------
# App Lab bricks
# ---------------------------------------------------------------------

logger = Logger("SmartAndonLedger")
ui = WebUI()

detection_stream = VideoObjectDetection(
    confidence=INITIAL_CONFIDENCE_THRESHOLD,
    debounce_sec=0.0
)


# ---------------------------------------------------------------------
# Persistent event log
# ---------------------------------------------------------------------

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = APP_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

EVENT_LOG = DATA_DIR / "andonledger_events.csv"

if not EVENT_LOG.exists():
    with EVENT_LOG.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "timestamp_utc",
            "old_state",
            "new_state",
            "active_lights",
            "max_confidence"
        ])

recent_events = deque(maxlen=RECENT_EVENT_LIMIT)


def write_event(old_state, new_state, active_lights, confidence):
    timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    lights_text = "+".join(sorted(active_lights)) if active_lights else "none"

    with EVENT_LOG.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            timestamp,
            old_state,
            new_state,
            lights_text,
            f"{confidence:.4f}"
        ])

    recent_events.appendleft({
        "timestamp": timestamp,
        "old_state": old_state,
        "new_state": new_state,
        "active_lights": sorted(active_lights),
        "confidence": round(float(confidence), 3)
    })


# ---------------------------------------------------------------------
# Machine-state engine
# ---------------------------------------------------------------------

lock = threading.RLock()

confirmed_state = "UNKNOWN"
candidate_state = "UNKNOWN"
candidate_since = time.monotonic()
state_started_at = time.monotonic()
last_valid_detection_at = time.monotonic()

latest_active_lights = set()
latest_max_confidence = 0.0
current_confidence_threshold = INITIAL_CONFIDENCE_THRESHOLD

totals = {
    "RUNNING": 0.0,
    "WARNING": 0.0,
    "FAULT": 0.0,
    "UNKNOWN": 0.0,
}

fault_count = 0
warning_count = 0
shift_started_at = datetime.now(timezone.utc)


def derive_machine_state(active_lights):
    """
    Resolve simultaneous detections into one machine state.

    Prototype priority:
        RED > YELLOW > GREEN

    Therefore:
        green                 -> RUNNING
        yellow                -> WARNING
        red                   -> FAULT
        green + yellow        -> WARNING
        green + red           -> FAULT
        yellow + red          -> FAULT
        green + yellow + red  -> FAULT

    Change this function if the actual machine uses another stack-light code.
    """
    if "red" in active_lights:
        return "FAULT"

    if "yellow" in active_lights:
        return "WARNING"

    if "green" in active_lights:
        return "RUNNING"

    return "UNKNOWN"


def extract_active_lights(detections):
    """
    Read every valid red/yellow/green detection in the current inference result.
    A frame can therefore contain one class or several classes.
    """
    active_lights = set()
    max_confidence = 0.0

    with lock:
        threshold = current_confidence_threshold

    for raw_label, instances in detections.items():
        label = str(raw_label).strip().lower()

        if label not in VALID_LABELS:
            continue

        for item in instances:
            confidence = float(item.get("confidence", 0.0) or 0.0)

            if confidence >= threshold:
                active_lights.add(label)
                max_confidence = max(max_confidence, confidence)

    return active_lights, max_confidence


def commit_state(new_state, active_lights, confidence):
    global confirmed_state
    global state_started_at
    global fault_count
    global warning_count

    now = time.monotonic()

    with lock:
        old_state = confirmed_state

        if new_state == old_state:
            return

        elapsed = max(0.0, now - state_started_at)
        totals[old_state] = totals.get(old_state, 0.0) + elapsed

        confirmed_state = new_state
        state_started_at = now

        if new_state == "FAULT":
            fault_count += 1

        if new_state == "WARNING":
            warning_count += 1

    write_event(
        old_state,
        new_state,
        active_lights,
        confidence
    )

    logger.info(
        f"STATE CHANGE: {old_state} -> {new_state} | "
        f"lights={sorted(active_lights)} | "
        f"confidence={confidence:.2f}"
    )


def process_detections(detections: dict):
    """
    Process one inference result.

    IMPORTANT:
    - Absence of "red" means red is not currently detected; there is no
      separate red_off class.
    - Empty detections do not immediately create UNKNOWN. The watchdog gives
      a short grace period so a brief transition gap does not become a false
      state event.
    - A new derived state must remain stable for STABLE_SECONDS before it is
      committed to the ledger.
    """
    global candidate_state
    global candidate_since
    global last_valid_detection_at
    global latest_active_lights
    global latest_max_confidence

    now = time.monotonic()

    active_lights, max_confidence = extract_active_lights(detections)

    # Brief no-detection frames are ignored here.
    # The watchdog moves to UNKNOWN only if the gap persists.
    if not active_lights:
        return

    new_state = derive_machine_state(active_lights)

    with lock:
        last_valid_detection_at = now
        latest_active_lights = set(active_lights)
        latest_max_confidence = max_confidence

        # Start a fresh stabilization timer when the derived state changes.
        if new_state != candidate_state:
            candidate_state = new_state
            candidate_since = now
            return

        should_commit = (
            new_state != confirmed_state
            and (now - candidate_since) >= STABLE_SECONDS
        )

    if should_commit:
        commit_state(
            new_state,
            active_lights,
            max_confidence
        )


def watchdog_loop():
    """
    A sustained period with no valid red/yellow/green detection is UNKNOWN,
    not FAULT. This separates camera/model visibility problems from machine
    faults.
    """
    global candidate_state
    global candidate_since
    global latest_active_lights
    global latest_max_confidence

    while True:
        time.sleep(0.2)
        now = time.monotonic()

        with lock:
            should_unknown = (
                (now - last_valid_detection_at) >= NO_SIGNAL_TIMEOUT_SECONDS
                and confirmed_state != "UNKNOWN"
            )

        if should_unknown:
            with lock:
                candidate_state = "UNKNOWN"
                candidate_since = now
                latest_active_lights = set()
                latest_max_confidence = 0.0

            commit_state(
                "UNKNOWN",
                set(),
                0.0
            )


# ---------------------------------------------------------------------
# IIoT metrics
# ---------------------------------------------------------------------

def live_totals():
    with lock:
        result = dict(totals)
        state = confirmed_state
        elapsed = max(0.0, time.monotonic() - state_started_at)

    result[state] = result.get(state, 0.0) + elapsed
    return result


def build_telemetry():
    current_totals = live_totals()

    running = current_totals["RUNNING"]
    warning = current_totals["WARNING"]
    fault = current_totals["FAULT"]
    unknown = current_totals["UNKNOWN"]

    # UNKNOWN is excluded because it is not a trustworthy machine condition.
    known_time = running + warning + fault

    availability = (
        (running / known_time) * 100.0
        if known_time > 0
        else 0.0
    )

    with lock:
        state = confirmed_state
        state_duration = max(0.0, time.monotonic() - state_started_at)
        active_lights = sorted(latest_active_lights)
        max_confidence = latest_max_confidence
        faults = fault_count
        warnings = warning_count
        threshold = current_confidence_threshold
        events = list(recent_events)

    return {
        "machine_state": state,
        "active_lights": active_lights,
        "confidence": round(max_confidence, 3),
        "threshold": round(threshold, 2),
        "state_duration_s": int(state_duration),
        "runtime_s": int(running),
        "warning_s": int(warning),
        "downtime_s": int(fault),
        "unknown_s": int(unknown),
        "fault_count": faults,
        "warning_count": warnings,
        "availability_pct": round(availability, 1),
        "long_stop": (
            state == "FAULT"
            and state_duration >= LONG_STOP_SECONDS
        ),
        "long_stop_threshold_s": int(LONG_STOP_SECONDS),
        "shift_started_utc": shift_started_at.isoformat(timespec="seconds"),
        "recent_events": events,
    }


def telemetry_loop():
    while True:
        try:
            payload = build_telemetry()
            ui.send_message("andon_update", payload)

        except Exception as exc:
            logger.info(f"Telemetry update failed: {exc}")

        time.sleep(TELEMETRY_PERIOD_SECONDS)


# ---------------------------------------------------------------------
# WebUI controls
# ---------------------------------------------------------------------

def set_threshold(client_id, threshold):
    global current_confidence_threshold

    try:
        value = float(threshold)
        value = max(0.10, min(1.00, value))

        with lock:
            current_confidence_threshold = value

        detection_stream.override_threshold(value)

        logger.info(
            f"Confidence threshold changed to {value:.2f}"
        )

    except (TypeError, ValueError):
        logger.info("Invalid confidence threshold received.")


def reset_shift(client_id, message=None):
    global totals
    global fault_count
    global warning_count
    global state_started_at
    global shift_started_at

    with lock:
        totals = {
            "RUNNING": 0.0,
            "WARNING": 0.0,
            "FAULT": 0.0,
            "UNKNOWN": 0.0,
        }

        fault_count = 0
        warning_count = 0
        state_started_at = time.monotonic()
        shift_started_at = datetime.now(timezone.utc)

    logger.info("Shift counters reset.")


ui.on_message("override_th", set_threshold)
ui.on_message("reset_shift", reset_shift)


# ---------------------------------------------------------------------
# Start
# ---------------------------------------------------------------------

detection_stream.on_detect_all(process_detections)

threading.Thread(
    target=watchdog_loop,
    daemon=True
).start()

threading.Thread(
    target=telemetry_loop,
    daemon=True
).start()

logger.info("Smart AndonLedger AI started.")
logger.info(
    "Mixed-light prototype priority: RED > YELLOW > GREEN."
)

App.run()
