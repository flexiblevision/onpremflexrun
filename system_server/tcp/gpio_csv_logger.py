import csv
import json
import os
import time

LOG_DIR = "/home/visioncell/Documents"
CSV_PATH = os.path.join(LOG_DIR, "gpio_inspection_log.csv")

CSV_HEADERS = [
    "result", "do2_set_ts", "do1_high_ts", "do1_low_ts", "do2_release_ts",
    "do1_pulse_ms", "do2_hold_ms", "total_ms", "warnings"
]

_current_cycle = None


def _ts_ms():
    return round(time.time() * 1000, 3)


def _ensure_csv():
    if not os.path.exists(CSV_PATH):
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(CSV_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)


def _write_row(cycle):
    warnings = "|".join(cycle.get("warnings", []))
    do2_set = cycle.get("do2_set_ts")
    do1_high = cycle.get("do1_high_ts")
    do1_low = cycle.get("do1_low_ts")
    do2_release = cycle.get("do2_release_ts")

    do1_pulse_ms = ""
    if do1_high is not None and do1_low is not None:
        do1_pulse_ms = round(do1_low - do1_high, 3)

    do2_hold_ms = ""
    if do2_set is not None and do2_release is not None:
        do2_hold_ms = round(do2_release - do2_set, 3)

    total_ms = ""
    if do2_set is not None and do2_release is not None:
        total_ms = round(do2_release - do2_set, 3)

    row = [
        cycle.get("result", ""),
        do2_set if do2_set is not None else "",
        do1_high if do1_high is not None else "",
        do1_low if do1_low is not None else "",
        do2_release if do2_release is not None else "",
        do1_pulse_ms,
        do2_hold_ms,
        total_ms,
        warnings,
    ]

    _ensure_csv()
    with open(CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(row)


def _flush_incomplete(cycle, extra_warnings=None):
    if extra_warnings:
        cycle.setdefault("warnings", []).extend(extra_warnings)
    if cycle.get("do1_high_ts") is None:
        cycle.setdefault("warnings", []).append("MISSING: DO1 never went HIGH")
    if cycle.get("do1_low_ts") is None:
        cycle.setdefault("warnings", []).append("MISSING: DO1 never went LOW")
    if cycle.get("do2_release_ts") is None:
        cycle.setdefault("warnings", []).append("MISSING: DO2 never released")
    _write_row(cycle)


def log_signal(raw: bytes):
    """Log a raw TCP signal payload for GPIO analysis.

    Call this with the raw bytes received from the TCP socket before
    passing them to the GPIO driver. Expected format: b'{"1": true}' etc.
    """
    global _current_cycle
    now = _ts_ms()

    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception:
        _ensure_csv()
        with open(CSV_PATH, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(["", "", "", "", "", "", "", "", f"PARSE_ERROR: {raw!r}"])
        return

    pin = str(list(parsed.keys())[0])
    value = parsed[pin]

    # DO2 (pin 2) — data/reject signal
    if pin == "2":
        if value is True:
            # DO2 HIGH = start of a FAIL cycle
            if _current_cycle is not None:
                _flush_incomplete(_current_cycle, ["NEW_CYCLE_BEFORE_COMPLETE"])
            _current_cycle = {
                "result": "FAIL",
                "do2_set_ts": now,
                "warnings": [],
            }
        else:
            # DO2 LOW
            if _current_cycle is not None:
                if _current_cycle.get("result") == "FAIL":
                    # Releasing DO2 after a FAIL cycle
                    _current_cycle["do2_release_ts"] = now
                    _write_row(_current_cycle)
                    _current_cycle = None
                else:
                    # PASS cycle — DO2 LOW is the start
                    # This case is handled below at cycle creation
                    _current_cycle["do2_release_ts"] = now
                    _write_row(_current_cycle)
                    _current_cycle = None
            else:
                # DO2 LOW with no active cycle = start of a PASS cycle
                _current_cycle = {
                    "result": "PASS",
                    "do2_set_ts": now,
                    "warnings": [],
                }

    # DO1 (pin 1) — shift register clock
    elif pin == "1":
        if _current_cycle is None:
            # Clock pulse outside a cycle — start an implicit PASS cycle
            _current_cycle = {
                "result": "PASS",
                "do2_set_ts": now,
                "warnings": [],
            }
        if value is True:
            _current_cycle["do1_high_ts"] = now
        else:
            _current_cycle["do1_low_ts"] = now


if __name__ == "__main__":
    import tempfile
    import os

    # Use a temp file for self-test
    _test_dir = tempfile.mkdtemp()
    CSV_PATH = os.path.join(_test_dir, "gpio_inspection_log.csv")
    LOG_DIR = _test_dir

    print(f"Self-test CSV: {CSV_PATH}\n")

    # --- FAIL cycle ---
    print("=== FAIL cycle ===")
    log_signal(b'{"2": true}')    # DO2 HIGH (FAIL)
    time.sleep(0.005)
    log_signal(b'{"1": true}')    # DO1 HIGH
    time.sleep(0.010)
    log_signal(b'{"1": false}')   # DO1 LOW
    time.sleep(0.005)
    log_signal(b'{"2": false}')   # DO2 LOW (release)

    # --- PASS cycle ---
    print("=== PASS cycle ===")
    log_signal(b'{"2": false}')   # DO2 LOW (PASS)
    time.sleep(0.005)
    log_signal(b'{"1": true}')    # DO1 HIGH
    time.sleep(0.010)
    log_signal(b'{"1": false}')   # DO1 LOW
    time.sleep(0.005)
    log_signal(b'{"2": false}')   # DO2 release

    # --- Stuck HIGH scenario ---
    print("=== Stuck HIGH (new cycle before complete) ===")
    log_signal(b'{"2": true}')    # DO2 HIGH (FAIL)
    time.sleep(0.005)
    log_signal(b'{"1": true}')    # DO1 HIGH
    # DO1 never goes LOW, DO2 never released
    log_signal(b'{"2": true}')    # New DO2 HIGH before previous cycle completes
    time.sleep(0.005)
    log_signal(b'{"1": true}')
    time.sleep(0.010)
    log_signal(b'{"1": false}')
    time.sleep(0.005)
    log_signal(b'{"2": false}')

    # --- Parse error ---
    print("=== Parse error ===")
    log_signal(b"not json")

    # Print results
    print("\n--- CSV Output ---")
    with open(CSV_PATH, "r") as f:
        print(f.read())

    # Cleanup
    os.remove(CSV_PATH)
    os.rmdir(_test_dir)
    print("Self-test complete.")
