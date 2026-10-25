from datetime import datetime
#生成会话标识（精确到秒，避免同一分钟新建多个会话时ID冲突）
def generate_session_id() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
#把会话ID转成短时间用于展示（ID本身仍保留完整时间做唯一标识）
def format_session_time(session_id: str) -> str:
    try:
        dt = datetime.strptime(session_id, "%Y-%m-%d %H:%M:%S")
        return dt.strftime("%m-%d %H:%M")
    except ValueError:
        return session_id
#生成每条交互内容的时间
def now_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")