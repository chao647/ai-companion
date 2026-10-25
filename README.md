# AI 伴侣

基于 Streamlit + OpenAI SDK 的 AI 聊天应用。支持流式回复、会话持久化、上下文摘要、性格预设和聊天记录导出。

## 功能

- 微信风格聊天界面，流式输出
- 会话管理：新建、恢复、重命名、单条删除、批量删除、一键清空
- 消息时间戳与 Markdown 聊天记录导出
- 上下文管理：保留最近消息 + 滚动摘要，避免超出模型上下文
- 首轮对话后自动生成会话标题
- 性格预设（温柔 / 幽默 / 毒舌 / 傲娇等）+ 自定义
- 可调参数：`temperature`、`max_tokens`、模型名
- 侧边栏数据统计（会话数、消息数、AI 调用次数）与耗时显示
- 日志按天轮转，记录内容不含聊天正文
- 数据库访问层与纯函数拆分，配套 pytest 单元测试

## 技术栈

- Python 3.10+
- Streamlit
- OpenAI Python SDK（兼容 OpenAI 接口的服务）
- SQLite

## 项目结构

| 文件 | 说明 |
|---|---|
| `AI-Company.py` | 主程序：页面渲染、交互与业务编排 |
| `config.py` | 提示词、性格预设、上下文与日志配置 |
| `db.py` | 数据库访问层（不依赖 Streamlit） |
| `utils.py` | 纯工具函数（时间格式化等） |
| `test_utils.py` / `test_db.py` | 单元测试 |
| `resource/` | 静态资源 |

## 快速开始

### 1. 安装依赖

```bash
pip install streamlit openai python-dotenv pytest
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env`，填入自己的配置：

```bash
AI_BASE_URL=https://api.deepseek.com
AI_MODEL=deepseek-chat
AI_API_KEY=your_api_key_here
```

`AI_BASE_URL` 和 `AI_MODEL` 按你使用的服务商填写。

### 3. 启动

```bash
streamlit run AI-Company.py
```

浏览器会自动打开 `http://localhost:8501`。

## 运行测试

```bash
python -m pytest -v
```

数据库测试使用 pytest 的 `tmp_path`，不会读写真实的 `ai_companion.db`。

## 数据与隐私

- 聊天记录保存在本地 `ai_companion.db`，不随代码提交
- `.env` 已在 `.gitignore` 中，API Key 不会被提交
- 日志只记录长度、会话 ID、耗时等信息，不记录聊天正文

## 说明

主程序导入时会执行整个 Streamlit 页面，因此可测试的逻辑都拆到了 `utils.py` 和 `db.py`。
