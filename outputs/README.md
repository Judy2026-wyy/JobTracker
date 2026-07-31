# 求职追踪看板（Job Tracker）

基于 Streamlit + SQLite 的求职投递/面试进度追踪应用。

## 功能

- 上传截图，用 GPT-4o Vision 识别公司、岗位、投递进度
- 连接 163 邮箱（IMAP），自动解析邮件中的投递/面试进度更新，预览确认后导入
- SQLite 本地数据库记录投递（applications）与面试（interviews）
- 首页提醒条：未来 48 小时内的面试，按时间升序展示（美国达拉斯 America/Chicago 本地时间）；
  时区未确认的记录单独分组，标注"时间待确认"
- 月视图日历（streamlit-calendar），展示所有面试卡片，支持切换月份查看历史
- 简单统计：各状态数量、投递→面试转化率
- 状态严格按枚举流转：已投递 → 简历筛选中 → 笔试 → 一面 → 二面 → 三面 → HR面 → 已收offer，
  任意阶段可转为 已拒绝 / 已终止

## 文件结构

```
app.py                主入口，页面路由
config.py             状态枚举、状态流转规则、时区常量
db.py                 SQLite 建表与全部读写（sqlite3 写入 / pandas.read_sql 查询）
timezone_utils.py     zoneinfo 时区转换（America/Chicago，自动处理夏令时）
llm_vision.py         截图识别（OpenAI GPT-4o Vision）
email_import.py       163邮箱 IMAP 读取 + LLM 解析邮件
ui_home.py            首页：提醒条 + 统计
ui_screenshot.py       截图上传 UI
ui_email_sync.py      邮箱同步 UI
ui_calendar.py         面试月历 UI
ui_applications.py    全部投递记录 UI（增改查删、状态流转、添加面试）
requirements.txt      依赖列表
.env.example           环境变量示例
```

## 本地部署步骤

### 1. 准备环境

需要 Python 3.10 及以上版本（`zoneinfo` 与部分类型标注语法要求）。

```bash
python3 --version   # 确认 >= 3.10
```

### 2. 创建虚拟环境并安装依赖

```bash
cd job_tracker  # 进入本项目所在目录
python3 -m venv .venv
source .venv/bin/activate     # Windows 用 .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，填入：

- `LLM_API_KEY`：OpenAI API Key（用于截图识别与邮件解析，调用 GPT-4o）
- `IMAP_HOST`：163邮箱 IMAP 服务器，默认 `imap.163.com`
- `IMAP_USER`：163邮箱完整地址
- `IMAP_PASS`：163邮箱的 **IMAP 授权码**（不是登录密码，需在163邮箱设置里单独开启 IMAP/SMTP 服务并生成授权码）

> 应用代码中已通过 `load_dotenv(find_dotenv(), override=True)` 加载 `.env`，无需额外配置。

### 4. 启动应用

```bash
streamlit run app.py
```

启动后浏览器会自动打开 `http://localhost:8501`。

### 5. 首次使用

1. 打开「全部记录」手动新增几条投递记录，或直接上传截图 / 同步邮箱
2. 在「添加面试安排」中填写面试时间和时区，未确认时区的会出现在首页"时间待确认"分组
3. 「面试月历」可切换月份查看历史与未来的面试安排

## 部署到其他环境的建议

- **同一局域网内共享**：`streamlit run app.py --server.address 0.0.0.0 --server.port 8501`，
  同网络下其他设备通过 `http://你的电脑IP:8501` 访问。
- **长期在服务器/云主机上运行**：建议用 `systemd` 或 `pm2`/`supervisor` 常驻进程，
  并在前面加 nginx 反向代理 + HTTPS。
- **Streamlit Community Cloud / Docker**：如需要，可在此基础上补充 `Dockerfile` 或
  推送到 GitHub 后连接 share.streamlit.io（需要你自己的账号，此环境无法代为操作）。

## 已知限制 / 注意事项

- LLM 识别结果可能有误，截图识别与邮件解析结果均设计为"预览-确认"后再写入数据库，
  请务必人工核对公司名、岗位名、状态与时间。
- 163 邮箱 IMAP 需要账号中显式开启 IMAP/SMTP 服务，并使用授权码而非登录密码。
- 邮件中若未明确写出时区，程序默认按 `Asia/Shanghai`（北京时间）解释原始时间再换算为
  America/Chicago，且该记录会被标记为 `timezone_confirmed=False`，需要人工确认。
- 转化率统计口径：已产生至少一条面试记录的投递数 / 总投递数（不依赖 status 字段，
  因为状态一旦转为"已拒绝/已终止"会覆盖此前的阶段信息）。
- `job_tracker.db` 请放在本地磁盘目录下运行（不要放在网络盘/云同步盘的实时同步路径中），
  部分网络文件系统对 SQLite 的文件锁支持不好，可能报 `disk I/O error`。

## 验证记录

已在沙盒环境完成以下验证：Python 3.10 下全部模块 `py_compile` 通过；`db.py` 的建表、
去重插入、非法状态流转拦截、面试查询、转化率统计均实测通过；`timezone_utils` 对 7 月
（夏令时期间）时间正确输出 `CDT`；`streamlit run app.py` 本地启动后 HTTP 200，页面无报错。
LLM 截图识别与163邮箱同步功能依赖真实的 `LLM_API_KEY` / `IMAP_USER` / `IMAP_PASS`，
请在你本机配置好 `.env` 后实测。
