import sqlite3

from db import init_db, save_session


def test_save_session_roundtrip(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    messages = [
        {"role": "user", "content": "你好", "created_at": "2026-10-25 10:00:00"},
        {"role": "assistant", "content": "你好呀", "created_at": "2026-10-25 10:00:01"},
    ]
    save_session(
        db_path,
        "2026-10-25 10:00:00",
        "小柔", "温柔", "", 0, "测试会话",
        messages,
    )

    with sqlite3.connect(db_path) as conn:
        session = conn.execute(
            "SELECT nick_name, title FROM sessions WHERE session_id = ?",
            ("2026-10-25 10:00:00",),
        ).fetchone()
        rows = conn.execute(
            "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id",
            ("2026-10-25 10:00:00",),
        ).fetchall()

    assert session == ("小柔", "测试会话")
    assert rows == [
        ("user", "你好", "2026-10-25 10:00:00"),
        ("assistant", "你好呀", "2026-10-25 10:00:01"),
    ]


def test_save_session_twice_does_not_duplicate(tmp_path):
    db_path = str(tmp_path / "test.db")
    init_db(db_path)

    messages = [{"role": "user", "content": "只应存一次", "created_at": "2026-10-25 10:00:00"}]
    for _ in range(2):
        save_session(db_path, "sid-1", "小柔", "温柔", "", 0, "标题", messages)

    with sqlite3.connect(db_path) as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE session_id = ?", ("sid-1",)
        ).fetchone()[0]

    assert count == 1
