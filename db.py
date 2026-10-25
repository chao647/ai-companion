"""数据库访问层：不依赖 Streamlit，数据库路径由调用方传入，方便单元测试。"""

import sqlite3
from contextlib import contextmanager


@contextmanager
def db_connection(db_path: str):
    """打开连接，退出 with 时无论是否异常都关闭。"""
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: str) -> None:
    """建表，并给旧数据库补齐新增的列。"""
    with db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sessions(
                session_id TEXT PRIMARY KEY,
                nick_name TEXT,
                nature TEXT,
                summary TEXT,
                title TEXT,
                summary_cursor INTEGER DEFAULT 0,
                created_at TEXT DEFAULT (datetime('now', 'localtime'))
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS messages(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN('user', 'assistant', 'system')),
                content TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions (session_id) ON DELETE CASCADE
            )
        """)

        # 兼容旧数据库：补充新增的列（已存在则跳过）
        cursor.execute("PRAGMA table_info(sessions)")
        existing_columns = {row[1] for row in cursor.fetchall()}
        if "summary" not in existing_columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN summary TEXT")
        if "summary_cursor" not in existing_columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN summary_cursor INTEGER DEFAULT 0")
        if "title" not in existing_columns:
            cursor.execute("ALTER TABLE sessions ADD COLUMN title TEXT")
        cursor.execute("PRAGMA table_info(messages)")
        message_columns = {row[1] for row in cursor.fetchall()}
        if "created_at" not in message_columns:
            cursor.execute("ALTER TABLE messages ADD COLUMN created_at TEXT")
        conn.commit()


def save_session(
    db_path: str,
    session_id: str,
    nick_name: str,
    nature: str,
    summary: str,
    summary_cursor: int,
    title: str,
    messages: list[dict],
) -> None:
    """整段覆盖保存一个会话：先写会话，再删旧消息、逐条写新消息。"""
    with db_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO sessions (session_id, nick_name, nature, summary, summary_cursor, title)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, nick_name, nature, summary, summary_cursor, title))
        cursor.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        for msg in messages:
            cursor.execute("""
                INSERT INTO messages (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
            """, (session_id, msg["role"], msg["content"], msg.get("created_at")))
        conn.commit()
