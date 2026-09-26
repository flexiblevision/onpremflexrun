"""CSV audit trails for the GPIO signalling path.

gpio_csv_logger reconstructs inspection cycles from a stream of individual pin
transitions, which is the only record of what the PLC actually saw. A cycle
that is silently dropped, or one whose timings are attributed to the wrong
cycle, makes the log worse than useless during a dispute.
"""
import csv
import json
import os
import pytest
from unittest.mock import patch

from tcp import gpio_csv_logger as logger
from tcp import gpio_error_logger as errlog


@pytest.fixture
def csv_path(tmp_path, monkeypatch):
    path = tmp_path / 'gpio_inspection_log.csv'
    monkeypatch.setattr(logger, 'LOG_DIR', str(tmp_path))
    monkeypatch.setattr(logger, 'CSV_PATH', str(path))
    monkeypatch.setattr(logger, '_current_cycle', None)
    return path


def _rows(path):
    """Rows written so far; an absent file means nothing was logged."""
    if not os.path.exists(path):
        return []
    with open(path, newline='') as f:
        return list(csv.DictReader(f))


class TestEnsureCsv:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_creates_the_file_with_headers(self, csv_path):
        logger._ensure_csv()

        with open(csv_path, newline='') as f:
            assert next(csv.reader(f)) == logger.CSV_HEADERS

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_existing_file_is_not_truncated(self, csv_path):
        logger._ensure_csv()
        with open(csv_path, 'a') as f:
            f.write('PASS,1,2,3,4,5,6,7,\n')

        logger._ensure_csv()

        assert len(_rows(csv_path)) == 1

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_log_directory_is_created(self, tmp_path, monkeypatch):
        nested = tmp_path / 'a' / 'b'
        monkeypatch.setattr(logger, 'LOG_DIR', str(nested))
        monkeypatch.setattr(logger, 'CSV_PATH', str(nested / 'log.csv'))

        logger._ensure_csv()

        assert (nested / 'log.csv').exists()


class TestFailCycle:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_complete_fail_cycle_is_written_once(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"1": true}')
        logger.log_signal(b'{"1": false}')
        logger.log_signal(b'{"2": false}')

        rows = _rows(csv_path)
        assert len(rows) == 1
        assert rows[0]['result'] == 'FAIL'
        assert rows[0]['warnings'] == ''

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_pulse_and_hold_durations_are_recorded(self, csv_path):
        times = iter([1000.0, 1010.0, 1025.0, 1040.0])
        with patch.object(logger, '_ts_ms', lambda: next(times)):
            logger.log_signal(b'{"2": true}')
            logger.log_signal(b'{"1": true}')
            logger.log_signal(b'{"1": false}')
            logger.log_signal(b'{"2": false}')

        row = _rows(csv_path)[0]
        assert float(row['do1_pulse_ms']) == 15.0
        assert float(row['do2_hold_ms']) == 40.0
        assert float(row['total_ms']) == 40.0

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_cycle_is_cleared_after_writing(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"2": false}')

        assert logger._current_cycle is None


class TestPassCycle:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_do2_low_with_no_open_cycle_starts_a_pass(self, csv_path):
        logger.log_signal(b'{"2": false}')

        assert logger._current_cycle['result'] == 'PASS'
        assert _rows(csv_path) == []

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_clock_pulse_outside_a_cycle_opens_an_implicit_pass(self, csv_path):
        logger.log_signal(b'{"1": true}')

        assert logger._current_cycle['result'] == 'PASS'
        assert logger._current_cycle['do1_high_ts'] is not None

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_complete_pass_cycle_is_written(self, csv_path):
        logger.log_signal(b'{"2": false}')
        logger.log_signal(b'{"1": true}')
        logger.log_signal(b'{"1": false}')
        logger.log_signal(b'{"2": false}')

        rows = _rows(csv_path)
        assert len(rows) == 1
        assert rows[0]['result'] == 'PASS'


class TestIncompleteCycles:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_new_cycle_flushes_the_previous_one_with_warnings(self, csv_path):
        # DO1 never went low and DO2 was never released before the next FAIL.
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"1": true}')
        logger.log_signal(b'{"2": true}')

        rows = _rows(csv_path)
        assert len(rows) == 1
        warnings = rows[0]['warnings'].split('|')
        assert 'NEW_CYCLE_BEFORE_COMPLETE' in warnings
        assert 'MISSING: DO1 never went LOW' in warnings
        assert 'MISSING: DO2 never released' in warnings

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_missing_clock_high_is_reported(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"2": true}')

        assert 'MISSING: DO1 never went HIGH' in _rows(csv_path)[0]['warnings']

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_incomplete_flush_leaves_durations_blank(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"2": true}')

        row = _rows(csv_path)[0]
        assert row['do1_pulse_ms'] == ''
        assert row['do2_release_ts'] == ''
        assert row['total_ms'] == ''

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_flush_starts_a_fresh_cycle(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"1": true}')
        logger.log_signal(b'{"2": true}')

        assert logger._current_cycle.get('do1_high_ts') is None
        assert logger._current_cycle['warnings'] == []


class TestMalformedSignals:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_unparseable_bytes_are_logged_as_a_parse_error(self, csv_path):
        logger.log_signal(b'not json')

        row = _rows(csv_path)[0]
        assert 'PARSE_ERROR' in row['warnings']
        assert row['result'] == ''

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_parse_error_does_not_disturb_an_open_cycle(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'\xff\xfe garbage')

        assert logger._current_cycle is not None
        assert logger._current_cycle['result'] == 'FAIL'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_unknown_pin_is_ignored(self, csv_path):
        logger.log_signal(b'{"7": true}')

        assert logger._current_cycle is None
        assert _rows(csv_path) == []

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_invalid_utf8_is_a_parse_error_not_a_crash(self, csv_path):
        logger.log_signal(b'\x80\x81')
        assert 'PARSE_ERROR' in _rows(csv_path)[0]['warnings']


class TestConsecutiveCycles:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_fail_then_a_pass_produce_two_rows(self, csv_path):
        logger.log_signal(b'{"2": true}')
        logger.log_signal(b'{"1": true}')
        logger.log_signal(b'{"1": false}')
        logger.log_signal(b'{"2": false}')

        logger.log_signal(b'{"2": false}')
        logger.log_signal(b'{"1": true}')
        logger.log_signal(b'{"1": false}')
        logger.log_signal(b'{"2": false}')

        assert [r['result'] for r in _rows(csv_path)] == ['FAIL', 'PASS']


# --------------------------------------------------------------------------
# gpio_error_logger
# --------------------------------------------------------------------------

@pytest.fixture
def error_csv(tmp_path, monkeypatch):
    path = tmp_path / 'gpio_error_log.csv'
    monkeypatch.setattr(errlog, 'LOG_DIR', str(tmp_path))
    monkeypatch.setattr(errlog, 'ERROR_CSV_PATH', str(path))
    return path


class TestGpioErrorLogger:
    @pytest.mark.unit
    @pytest.mark.gpio
    def test_logging_is_off_by_default(self, error_csv):
        # Every set_pin_state call goes through this; writing a CSV row per
        # GPIO transition on a production line would be a lot of IO.
        assert errlog.DEBUG is False

        errlog.log_gpio_error('set_gpio', 5, 1, 0, 0)

        assert not error_csv.exists()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_a_call_is_recorded_when_debug_is_on(self, error_csv, monkeypatch):
        monkeypatch.setattr(errlog, 'DEBUG', True)

        errlog.log_gpio_error('set_gpio', 5, 1, 0, -1)

        row = _rows(error_csv)[0]
        assert row['function'] == 'set_gpio'
        assert row['pin'] == '5'
        assert row['direction'] == '1'
        assert row['value'] == '0'
        assert row['return_code'] == '-1'
        assert row['error'] == ''

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_an_exception_is_stringified_into_the_row(self, error_csv, monkeypatch):
        monkeypatch.setattr(errlog, 'DEBUG', True)

        errlog.log_gpio_error('set_gpio', 5, 1, 0, -1, OSError('device busy'))

        assert _rows(error_csv)[0]['error'] == 'device busy'

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_headers_are_written_once(self, error_csv, monkeypatch):
        monkeypatch.setattr(errlog, 'DEBUG', True)

        errlog.log_gpio_error('set_gpio', 1, 1, 0, 0)
        errlog.log_gpio_error('read_gpi', 2, 0, 1, 0)

        with open(error_csv, newline='') as f:
            rows = list(csv.reader(f))
        assert rows[0] == errlog.ERROR_HEADERS
        assert len(rows) == 3

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_the_log_directory_is_created(self, tmp_path, monkeypatch):
        nested = tmp_path / 'x' / 'y'
        monkeypatch.setattr(errlog, 'DEBUG', True)
        monkeypatch.setattr(errlog, 'LOG_DIR', str(nested))
        monkeypatch.setattr(errlog, 'ERROR_CSV_PATH', str(nested / 'err.csv'))

        errlog.log_gpio_error('set_gpio', 1, 1, 0, 0)

        assert (nested / 'err.csv').exists()

    @pytest.mark.unit
    @pytest.mark.gpio
    def test_timestamps_are_iso_like(self, error_csv, monkeypatch):
        monkeypatch.setattr(errlog, 'DEBUG', True)
        errlog.log_gpio_error('set_gpio', 1, 1, 0, 0)

        import re
        assert re.fullmatch(r'\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}',
                            _rows(error_csv)[0]['timestamp'])
