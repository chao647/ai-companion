import streamlit as st
from openai import OpenAI
from dotenv import load_dotenv
import os
import time
from contextlib import contextmanager

load_dotenv()

import sqlite3
import html
import logging

import db
from logging.handlers import TimedRotatingFileHandler

AI_BASE_URL = os.environ.get('AI_BASE_URL')
AI_MODEL = os.environ.get('AI_MODEL')
AI_API_KEY = os.environ.get('AI_API_KEY')

from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ai_companion.db")
logger = logging.getLogger("ai_companion")

from config import (
    MAX_RECENT,
    SUMMARY_MAX_CHARS,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_TOKENS,
    LOG_DIR,
    LOG_FORMAT,
    LOG_CONSOLE_LEVEL,
    LOG_FILE_LEVEL,
    LOG_BACKUP_DAYS,
    NATURE_PRESETS,
    SYSTEM_PROMPT,
)

from utils import format_session_time, generate_session_id, now_str

#自定义异常类
class ChatAppError(Exception):
    """聊天应用基础异常类"""
    pass

class SessionError(ChatAppError):
    """会话基础操作异常"""
    pass

class SessionNotFoundError(SessionError):
    """会话不存在"""
    pass

class SessionSaveError(SessionError):
    """会话保存异常"""
    pass

class AIServiceError(ChatAppError):
    """AI服务异常"""
    pass

#设置页面配置项
st.set_page_config(
    page_title="AI聊天机器人",
    page_icon="🤖",
#布局
    layout="wide",

#控制侧边栏
    initial_sidebar_state="expanded",

#菜单项
    menu_items={ }
)

#==========函数层==========#
#初始化日志系统（控制台 + 按天轮转的文件）
def init_logging() -> None:
    # 日志目录不存在则创建
    os.makedirs(LOG_DIR, exist_ok=True)

    # 防止 Streamlit 重跑时重复添加 handler
    if logger.handlers:
        return

    logger.setLevel(logging.DEBUG)

    formatter = logging.Formatter(LOG_FORMAT)

    # 控制台 handler：只显示 INFO 及以上
    console = logging.StreamHandler()
    console.setLevel(LOG_CONSOLE_LEVEL)
    console.setFormatter(formatter)
    logger.addHandler(console)

    # 文件 handler：记录全部级别，每天一个文件，保留 7 天
    file_handler = TimedRotatingFileHandler(
        os.path.join(LOG_DIR, "app.log"),
        when="midnight",
        backupCount=LOG_BACKUP_DAYS,
        encoding="utf-8",
    )
    file_handler.setLevel(LOG_FILE_LEVEL)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.propagate = False

#初始化数据库
def init_db() -> None:
    db.init_db(DB_PATH)

#获取数据库连接
def get_db_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

@contextmanager
def db_connection():
    conn = get_db_connection()
    try:
        yield conn
    finally:
        conn.close()

#加载所有会话列表（返回会话ID和昵称）
def get_session_list() -> list[tuple[str, str, str]]:
    with db_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, nick_name, title FROM sessions ORDER BY created_at DESC")
        rows = cursor.fetchall()
        return [(row[0], row[1] or "", row[2] or "") for row in rows]

#读取最近的日志（默认最后50行，倒序显示）
def read_recent_logs(lines: int = 50) -> str:
    log_path = os.path.join(LOG_DIR, "app.log")
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.readlines()
    except OSError:
        return "暂无日志（还没产生任何日志记录）"
    return "".join(reversed(content[-lines:]))

#把旧摘要和新挤出的消息合并成新摘要
def summarize_history(old_summary: str, new_messages: list[dict[str, str]]) -> str:
    new_text = "\n".join(f"{m['role']}: {m['content']}" for m in new_messages)
    prompt = f"""请把下面的「旧摘要」和「新对话」合并压缩成一段不超过{SUMMARY_MAX_CHARS}字的摘要。
只保留：关键事实、用户偏好、正在讨论的话题、重要约定；省略寒暄和无关内容。

旧摘要：
{old_summary or '（无）'}

新对话：
{new_text}"""
    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[{"role": "user", "content": prompt}],
    )
    return response.choices[0].message.content.strip()

#根据首轮对话生成标题
def generate_title(user_msg: str, ai_msg: str) -> str:
    prompt = f"""请根据下面这轮对话，给这个会话起一个简短标题。
要求：
1. 不超过10个字
2. 只输出标题本身，不要引号、不要标点、不要任何解释
用户：{user_msg}
AI：{ai_msg}"""
    try:
        response = client.chat.completions.create(
            model=AI_MODEL,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content or ""
        title = raw.strip().replace("\n", " ")
        title = title.strip('"\'“”「」《》').strip()
        if not title:
            # 拿到空内容时记录截断原因，方便排查
            logger.warning("标题生成为空，finish_reason=%s，原始长度=%d",
                           response.choices[0].finish_reason, len(raw))
            return ""
        logger.info("标题生成成功，长度 %d", len(title))
        return title[:20]
    except Exception as e:
        logger.warning("标题生成失败，跳过：%s", e)
        return ""

#增量更新滚动摘要：只把新被挤出窗口的消息并入摘要
def update_summary() -> None:
    messages = st.session_state.messages
    overflow = len(messages) - MAX_RECENT
    if overflow > st.session_state.summary_cursor:
        new_messages = messages[st.session_state.summary_cursor:overflow]
        try:
            st.session_state.summary = summarize_history(st.session_state.summary, new_messages)
            st.session_state.summary_cursor = overflow
            logger.info("摘要已更新：并入 %d 条，游标 %d", len(new_messages), overflow)
        except Exception as e:
            # 摘要失败不阻断主回复，沿用旧摘要继续
            logger.exception("摘要更新失败，沿用旧摘要: %s", e)

#恢复指定会话
def restore_session(session_id :str) -> None:
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT nick_name, nature, summary, summary_cursor, title FROM sessions WHERE session_id = ?",
                (session_id,)
            )
            session_row = cursor.fetchone()
            if session_row:
                st.session_state.nick_input = session_row[0] or ""  #nick_name
                st.session_state.nature_input = session_row[1] or ""  #nature
                st.session_state.summary = session_row[2] or ""  #summary
                st.session_state.summary_cursor = session_row[3] or 0  #summary_cursor
                st.session_state.title = session_row[4] or ""
                st.session_state.current_session = session_id
                cursor.execute(
                    "SELECT role, content, created_at FROM messages WHERE session_id = ? ORDER BY id",
                    (session_id,)
                )
                message_rows = cursor.fetchall()
                st.session_state.messages = [
                    {"role": row[0], "content": row[1], "created_at":row[2] or ""}
                    for row in message_rows
                ]
                logger.info("会话已恢复: %s，消息数 %d", session_id, len(message_rows))
            else:
                raise SessionNotFoundError(f"会话不存在: {session_id}")
    except sqlite3.Error as e:
        logger.exception("会话恢复失败: %s", session_id)
        raise SessionError(f"会话恢复失败: {session_id}") from e

#删除指定会话
def delete_session(session_id :str) -> None:
    # 复用批量删除，避免两份相同的 SQL
    delete_sessions([session_id])

#删除多个选中的会话
def delete_sessions(session_ids : list[str]) -> None:
    if not session_ids:
        return
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            placeholder = ", ".join(["?"]*len(session_ids))
            cursor.execute(f"DELETE FROM messages WHERE session_id IN ({placeholder})", session_ids)
            cursor.execute(f"DELETE FROM sessions WHERE session_id IN ({placeholder})", session_ids)
            conn.commit()
            logger.info("已批量删除会话 %d 个", len(session_ids))
    except sqlite3.Error as e:
        logger.exception("批量删除会话失败")
        raise SessionError("批量删除会话失败") from e

#一键清空会话
def clear_all_sessions()-> None:
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM messages")
            cursor.execute("DELETE FROM sessions")
            conn.commit()
            logger.info("已清空全部会话")
    except sqlite3.Error as e:
        logger.exception("清空会话失败")
        raise SessionError("清空会话失败") from e

#修改会话昵称
def rename_session(session_id :str, new_title:str) -> None:

    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE sessions SET title = ? WHERE session_id = ?",
            (new_title, session_id)
            )
            conn.commit()
            logger.info("会话成功重命名: %s", session_id)
    except sqlite3.Error as e:
        logger.exception("会话重命名失败: %s", session_id)
        raise SessionSaveError(f"会话重命名失败: {session_id}") from e

#保存当前会话信息
def save_session(session_id:str|None = None) -> None:
    # 使用传入的session_id或当前的current_session
    current_id = session_id if session_id else st.session_state.current_session
    if current_id:
        if st.session_state.messages or st.session_state.nick_input or st.session_state.nature_input:
            try:
                db.save_session(
                    DB_PATH,
                    current_id,
                    st.session_state.nick_input,
                    st.session_state.nature_input,
                    st.session_state.summary,
                    st.session_state.summary_cursor,
                    st.session_state.title,
                    st.session_state.messages,
                )
                logger.info("会话已保存: %s，消息数 %d", current_id, len(st.session_state.messages))
            except sqlite3.Error as e:
                logger.exception("会话保存失败: %s", current_id)
                raise SessionSaveError(f"会话保存失败: {current_id}") from e

#构造单条消息的HTML片段（纯函数：只拼字符串，不渲染）
def message_to_html(role: str, content: str, created_at: str = "") -> str:
    avatar = "🤖" if role == "assistant" else "🧑"
    safe_content = html.escape(content).replace("\n", "<br>")
    title_attr = f' title="{html.escape(created_at)}"' if created_at else ""
    return (
        f'<div class="chat-row {role}">'
        f'<div class="chat-avatar">{avatar}</div>'
        f'<div class="chat-bubble"{title_attr}>{safe_content}</div>'
        f'</div>'
    )

#渲染单条消息（保持原签名，供"用户刚发出消息"时即时渲染用）
def render_message(role: str, content: str, created_at: str = "") -> None:
    st.markdown(message_to_html(role, content, created_at), unsafe_allow_html=True)

#一次性渲染整个聊天区（拼成一个完整闭合的HTML块，不跨调用开合标签）
def render_all_messages(messages: list[dict[str, str]]) -> None:
    body = "".join(message_to_html(m["role"], m["content"], m.get("created_at", ""))
                   for m in messages
                   )
    st.markdown(f'<div class="chat-container">{body}</div>', unsafe_allow_html=True)

#拼接markdown
def export_session_markdown() -> str:
    title = (
        st.session_state.title
        or st.session_state.nick_input
        or st.session_state.current_session
    )
    lines = [
        f"# {title}",
        "",
        f"- 昵称: {st.session_state.nick_input or '未设置'}",
        f"- 性格: {st.session_state.nature_input or '未设置'}",
        f"- 导出时间: {now_str()}",
        "",
        "---",
        "",
    ]
    for msg in st.session_state.messages:
        role_label = "🧑 用户" if msg["role"] == "user" else "🤖 AI"
        ts = msg.get("created_at", "")
        header = f"**{role_label}**"
        if ts:
            header += f"({ts})"
        lines.append(header)
        lines.append("")
        lines.append(msg["content"])
        lines.append("")
    return "\n".join(lines)

def reset_session_state() -> None:
    """清空当前会话的内存状态，准备开新会话"""
    st.session_state.messages = []
    st.session_state.current_session = generate_session_id()
    st.session_state.nick_input = ""
    st.session_state.nature_input = ""
    st.session_state.summary = ""
    st.session_state.summary_cursor = 0
    st.session_state.title = ""
    st.session_state.renaming_session = None
    st.session_state.last_elapsed = None

#统计查询函数
def get_stats() -> dict[str, int]:
    try:
        with db_connection() as conn:
            cursor = conn.cursor()
            sessions = cursor.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
            messages = cursor.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            ai_calls = cursor.execute(
                "SELECT COUNT(*) FROM messages WHERE role = 'assistant'"
            ).fetchone()[0]
            return {"sessions": sessions, "messages": messages, "ai_calls": ai_calls}
    except sqlite3.Error as e:
        logger.exception("统计查询失败")
        raise SessionError("统计查询失败") from e

#==========================#

#大标题
st.title("AI聊天机器人")

#logo
logo_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resource", "logo.png")
st.logo(logo_path)

#初始化会话状态
if "messages" not in st.session_state:
    st.session_state.messages = []

#初始化日志系统
init_logging()

#初始化数据库
init_db()

#昵称输入框
if "nick_input" not in st.session_state:
    st.session_state.nick_input = ""

#性格输入框
if "nature_input" not in st.session_state:
    st.session_state.nature_input = ""

#当前会话
if "current_session" not in st.session_state:
    st.session_state.current_session = generate_session_id()

#历史摘要与摘要游标
if "summary" not in st.session_state:
    st.session_state.summary = ""
if "summary_cursor" not in st.session_state:
    st.session_state.summary_cursor = 0
if "title" not in st.session_state:
    st.session_state.title = ""
if "renaming_session" not in st.session_state:
    st.session_state.renaming_session = None
if "confirm_clear" not in st.session_state:
    st.session_state.confirm_clear = False

#创建与AI大模型交互的客户端对象
api_key = os.environ.get('AI_API_KEY')
if not api_key:
    st.error("❌ 未配置 AI_API_KEY 环境变量，请在.env文件中配置")
    st.stop()

client = OpenAI(
    api_key = api_key,
    base_url=AI_BASE_URL)

#回调函数
def apply_preset() -> None:
    preset = st.session_state.nature_preset
    if preset != "自定义":
        st.session_state.nature_input = NATURE_PRESETS[preset]

#侧边栏
with st.sidebar:
    st.subheader("欢迎使用AI聊天机器人")
    #控制面板
    st.subheader("控制面板")

    #新建会话按钮
    if st.button("新建会话",width = "stretch"):
        if st.session_state.messages or st.session_state.nick_input or st.session_state.nature_input:
            #保存当前会话信息
            save_session()
        #创建新会话(清空对话内容）
        reset_session_state()
        st.rerun()
#===========UI层==========#
    #历史会话列表
    st.subheader("历史会话")
    session_list = get_session_list()
    if session_list:
        for session_id, nick_name, title in session_list:
            is_selected = (st.session_state.current_session == session_id)

            # 使用columns分成历史，重命名，删除三个部分
            col_history, col_rename, col_delete = st.columns(
                [3, 1, 1],
                vertical_alignment="center"
            )

            with col_history:
                # 同时显示昵称和短时间，没有昵称时只显示短时间
                time_label = format_session_time(session_id)
                if title:
                    display_name = f"{title}"
                elif nick_name:
                    display_name = f"{nick_name} · {time_label}"
                else:
                    display_name = time_label
                # 使用button的type参数实现高亮效果
                btn_type = "primary" if is_selected else "secondary"
                btn_label = f"✓ {display_name}" if is_selected else display_name
                if st.button(btn_label, key=f"load_{session_id}", type=btn_type, width="stretch"):
                    try:
                        restore_session(session_id)
                    except SessionError as e:
                        st.error(f"❌ {e}")
                    else:
                        st.rerun()
            with col_rename:
                if st.button("", icon=":material/edit:", type="tertiary", key=f"ren_{session_id}", width="stretch"):
                    st.session_state.renaming_session = session_id
                    st.rerun()

            with col_delete:
                if st.button("", icon=":material/delete:", type="tertiary", key=f"del_{session_id}", width="stretch"):

                    try:
                        delete_session(session_id)
                    except SessionError as e:
                        st.error(f"❌ {e}")
                    else:
                        # 如果删除的是当前会话，清空状态
                        if st.session_state.current_session == session_id:
                            reset_session_state()
                        if st.session_state.renaming_session == session_id:
                            st.session_state.renaming_session = None
                        st.rerun()
    else:
        st.info("暂无历史会话")

    #重命名编辑区
    if st.session_state.renaming_session:
        sid = st.session_state.renaming_session
        #从会话列表中找到当前会话的标题
        old_title = next((t or n for s, n, t in session_list if s == sid), "")
        new_name = st.text_input(
            "新名称",
            value = old_title,
            key = f"rename_input_{sid}",
        )
        col_ok, col_cancel = st.columns(2)
        with col_ok:
            if st.button("确定",key = "rename_ok", width="stretch"):
                new_name = new_name.strip()
                if not new_name:
                    st.warning("名称不能为空")
                else:
                    try:
                        rename_session(sid, new_name)
                    except SessionError as e:
                        st.error(f"❌ {e}")
                    else:
                        if sid == st.session_state.current_session:
                            st.session_state.title = new_name
                        st.session_state.renaming_session = None
                        st.rerun()
        with col_cancel:
            if st.button("取消",key = "rename_cancel", width="stretch"):
                st.session_state.renaming_session = None
                st.rerun()
    #清空选中的会话
    if session_list:
        session_labels = {
            sid: (title or (f"{nick} · {format_session_time(sid)}"if nick else format_session_time(sid)))
            for sid, nick, title in session_list
        }
        selected_ids = st.multiselect(
            "选择要删除的会话",
            options=list(session_labels.keys()),
            format_func=lambda sid: session_labels[sid],
            key="batch_delete_ids",
        )
        if selected_ids:
            if st.button(f"删除选中的 {len(selected_ids)} 个会话", width="stretch"):
                try:
                    delete_sessions(selected_ids)
                except SessionError as e:
                    st.error(f"❌ {e}")
                else:
                    if st.session_state.current_session in selected_ids:
                        reset_session_state()
                    if st.session_state.renaming_session in selected_ids:
                        st.session_state.renaming_session = None
                    st.rerun()

    #下载会话按钮
    st.download_button(
        "📥 导出当前会话",
        data = export_session_markdown(),
        file_name = f"聊天记录_{datetime.now().strftime('%Y%m%d%H%M%S')}.md",
        mime = "text/markdown",
        width = "stretch",
        disabled = not st.session_state.messages,
    )

    #清空全部会话
    st.divider()
    if st.button("🗑️ 清空全部会话", width="stretch"):
        st.session_state.confirm_clear = True
        st.rerun()
    if st.session_state.get("confirm_clear"):
        st.warning("确定要清空全部会话吗？此操作不可恢复。")
        col_yes, col_no = st.columns(2)
        with col_yes:
            if st.button("确认清空", key="clear_yes", type="primary", width="stretch"):
                try:
                    clear_all_sessions()
                except SessionError as e:
                    st.error(f"❌ {e}")
                else:
                    st.session_state.confirm_clear = False
                    reset_session_state()
                    st.rerun()
        with col_no:
            if st.button("取消", key="clear_no", width="stretch"):
                st.session_state.confirm_clear = False
                st.rerun()

    #昵称输入框
    st.text_input("昵称",placeholder="请输入昵称", key="nick_input")
    
    #性格输入框
    st.selectbox(
        "性格预设",
        options=list(NATURE_PRESETS.keys()),
        index=len(NATURE_PRESETS)-1,
        key="nature_preset",
        on_change=apply_preset,
    )
    st.text_area("性格", placeholder="请输入性格", key="nature_input")

    #添加控件
    with st.expander("⚙️ 生成参数", expanded=False):
        st.slider(
            "温度（越高越有创意）",
            min_value=0.0,
            max_value=2.0,
            value=DEFAULT_TEMPERATURE,
            step=0.1,
            key="temperature",
        )
        st.number_input(
            "最大回复长度（token）",
            min_value=100,
            max_value=4000,
            value=DEFAULT_MAX_TOKENS,
            step=100,
            key="max_tokens",
        )
        st.text_input("模型", value=AI_MODEL,key="model_input")

    #数据统计
    with st.expander("📊 统计", expanded=False):
        stats = get_stats()
        st.metric("会话数", stats["sessions"])
        st.metric("消息数", stats["messages"])
        st.metric("AI 调用", stats["ai_calls"])

    #日志查看
    with st.expander("📋 日志查看", expanded=False):
        log_lines = st.selectbox("显示行数", [20, 50, 100, 200], index=1)
        st.code(read_recent_logs(log_lines), language="text")

wechat_css = """
<style>
header[data-testid="stHeader"] {
    background: transparent;
}
[data-testid="stToolbarActions"],
[data-testid="stAppDeployButton"],
[data-testid="stMainMenu"],
[data-testid="stStatusWidget"] {
    display: none;
}
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 0 !important;
}
.chat-container {
    display: flex;
    flex-direction: column;
    gap: 16px;
    padding: 10px 0;
}
.chat-row {
    display: flex;
    align-items: flex-start;
    gap: 10px;
}
.chat-row.user {
    flex-direction: row-reverse;
}
.chat-row.assistant {
    flex-direction: row;
}
.chat-avatar {
    width: 40px;
    height: 40px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    font-size: 20px;
    flex-shrink: 0;
    background: #e0e0e0;
}
.chat-bubble {
    max-width: 65%;
    padding: 10px 14px;
    border-radius: 6px;
    font-size: 15px;
    line-height: 1.6;
    word-break: break-word;
    position: relative;
}
.chat-row.user .chat-bubble {
    background: #95ec69;
    color: #000;
    border-top-right-radius: 2px;
}
.chat-row.assistant .chat-bubble {
    background: #ffffff;
    color: #000;
    border: 1px solid #e0e0e0;
    border-top-left-radius: 2px;
}
.chat-bubble p {
    margin: 0;
}
</style>
"""
st.markdown(wechat_css, unsafe_allow_html=True)

#展示聊天信息（一次 markdown 调用，HTML 标签完整闭合）
render_all_messages(st.session_state.messages)
if st.session_state.get("last_elapsed") is not None:
    st.caption(f"⏱ 上次回复耗时 {st.session_state.last_elapsed:.2f} 秒")
#输入框
prompt = st.chat_input("请输入你的问题")

if prompt: #字符串会自动转换成布尔值
    logger.info("用户发送消息，准备调用AI大模型（内容不入日志，保护隐私）")
    #保存用户输入的提示词（昵称/性格由 save_session 写入 nick_name/nature 列）
    ts = now_str()
    st.session_state.messages.append({"role":"user", "content":prompt, "created_at":ts})
    save_session()
    render_message("user", prompt, ts)

#刷新后，调用大模型进行回复
if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
    try:
        # 增量更新滚动摘要（把超出窗口的旧消息压进摘要）
        update_summary()

        # 组装系统提示词：角色设定 + 历史摘要
        system_content = SYSTEM_PROMPT % (st.session_state.nick_input, st.session_state.nature_input)
        if st.session_state.summary:
            system_content += "\n\n【历史摘要】\n" + st.session_state.summary

        history = [
            {"role": m["role"],"content": m["content"]}
            for m in st.session_state.messages[-MAX_RECENT:]
        ]
        logger.info("本次发送给AI的历史条数: %d，摘要长度: %d", len(history), len(st.session_state.summary))

        start = time.perf_counter()
        response = client.chat.completions.create(
            model=st.session_state.model_input,
            messages=[
                 {"role": "system", "content": system_content},
                 *history
            ],
            stream=True,
            temperature=st.session_state.temperature,
            max_tokens=st.session_state.max_tokens,
        )

        # 输出结果（流式输出的解析方式）
        response_message = st.empty()#创建空组件存放大模型返回结果
        full_response = ""

        for chunk in response:
            if chunk.choices and chunk.choices[0].delta.content is not None:
                content = chunk.choices[0].delta.content
                full_response += content
                safe_response = html.escape(full_response).replace("\n", "<br>")
                response_message.markdown(f"""
                                   <div class="chat-container">
                                       <div class="chat-row assistant">
                                           <div class="chat-avatar">🤖</div>
                                           <div class="chat-bubble">{safe_response}</div>
                                       </div>
                                   </div>
                               """, unsafe_allow_html=True)
        elapsed = time.perf_counter() - start
        st.session_state.last_elapsed = elapsed
        logger.info("AI 回复耗时 %.2f 秒", elapsed)

        # 保存大模型返回结果
        st.session_state.messages.append({"role":"assistant",
                                          "content":full_response,
                                          "created_at":now_str()})
        logger.info("AI回复生成完成，长度 %d 字", len(full_response))
        try:
            save_session()
        except SessionSaveError as e:
            st.error(f"💾 会话保存失败，但回复已生成：{e}")
    except Exception as e:
        logger.exception("AI服务异常: %s", e)
        st.error(f"🤖 AI服务异常：{e}")
        # 确保撤回的是用户消息（而不是已经成功追加的 assistant 消息）
        if st.session_state.messages and st.session_state.messages[-1]["role"] == "user":
            st.session_state.messages.pop()

    if not st.session_state.title and len(st.session_state.messages) >= 2:
        st.session_state.title = generate_title(
            st.session_state.messages[0]["content"],
            st.session_state.messages[1]["content"],
        )
        if st.session_state.title:
            save_session()
    st.rerun()