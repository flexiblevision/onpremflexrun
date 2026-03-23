import csv
import os
import time

DEBUG = False

LOG_DIR = "/home/visioncell/Documents"
ERROR_CSV_PATH = os.path.join(LOG_DIR, "gpio_error_log.csv")

ERROR_HEADERS = ["timestamp", "function", "pin", "direction", "value", "return_code", "error"]


def _ts_iso():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())


def _ensure_error_csv():
    if not os.path.exists(ERROR_CSV_PATH):
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(ERROR_CSV_PATH, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(ERROR_HEADERS)


def log_gpio_error(function_name, pin, direction, value, return_code, error=None):
    """Log a GPIO C function call result when DEBUG is enabled.

    Args:
        function_name: Name of the C function called (e.g. 'set_gpio', 'read_gpi')
        pin: Pin number
        direction: Direction (0=input, 1=output)
        value: Value set (0=HIGH, 1=LOW for set_gpio)
        return_code: Return code from the C function
        error: Optional exception or error message
    """
    if not DEBUG:
        return

    _ensure_error_csv()
    row = [
        _ts_iso(),
        function_name,
        pin,
        direction,
        value,
        return_code,
        str(error) if error else "",
    ]
    with open(ERROR_CSV_PATH, "a", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(row)
