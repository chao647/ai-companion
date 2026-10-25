import re

from utils import format_session_time, generate_session_id, now_str


def test_format_session_time_valid():
    assert format_session_time("2026-10-25 09:05:30") == "10-25 09:05"


def test_format_session_time_invalid_returns_input():
    assert format_session_time("not-a-time") == "not-a-time"


def test_generate_session_id_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", generate_session_id())


def test_now_str_format():
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", now_str())
