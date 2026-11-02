# 架构说明

本工具只做一件事：把 Qoder CN 桌面端会话转录里的 usage 变成可读的实时/历史用量视图。设计目标是**只读、无依赖、单机、低延迟**。

## 数据流

```text
~/.qoder-cn/projects/
  <project>/<session>.jsonl                主链往返
  <project>/<session>/subagents/*.jsonl    子代理往返
        │
        ▼
src/cn_usage_core.py
  scan_all()      全量扫描（启动 / 静态生成）
  parse_line()    单行解析与字段归一
  summarize()     汇总 trips / tokens / credits / free / sub
  build_range()   按日 / 模型 / 项目聚合
        │
        ├─ src/build_cn_token_dashboard.py
        │     └─ dist/ 静态 HTML + JSON + Canvas 快照
        │
        └─ src/live_server.py
              LiveStore
                ├─ 初始全量扫描
                ├─ 每 1s 增量 tail（支持半行 / 文件截断）
                ├─ 每 2s 发现新增/删除的转录
                ├─ 0.7s 快照缓存
                └─ 本机 HTTP 服务 + 置顶小窗
```

## 模块职责

### `src/cn_usage_core.py`

- `ROOT`：默认 `Path.home() / ".qoder-cn" / "projects"`，可用 `QODER_CN_PROJECTS` 或 `--root` 覆盖。
- `WINDOW`：推导 Tokens 的窗口，默认 200,000，可用 `QODER_CN_WINDOW` 覆盖。
- `parse_line()`：只接受同时含 `message.usage.context_usage_ratio > 0` 与合法时间戳的行。
- `summarize()`：返回 trips、subTrips、freeTrips、sessions、projects、days、tokens、subTokens、subShare、freeTokens、freeShare、credits、origCredits、avgTrip、avgDay。
- `rows_for()` / `build_range()`：按日期、模型、项目聚合，并计算占比。
- `project_label()`：把工作区路径转成短标签；Windows/POSIX 路径都支持；`Documents/Qoder/<date>/<session>` 临时目录会显示为“Qoder 临时会话 <date>”。

### `src/live_server.py`

- `FileState`：单个 `.jsonl` 的增量读取状态。记录 `offset`、`pending` 和已解析记录。
  - 文件变短：视为截断，重置并重新读取。
  - 半行写入：留在 `pending`，下次拼接。
  - 最后一行没有换行：JSON 完整时直接消费；不完整则继续等待。
- `LiveStore`：持有所有 `FileState` 与全局记录列表。
  - 初始全量扫描；之后每 1 秒 tail 已发现文件，每 2 秒发现新文件/删除文件。
  - 记录变化后重建排序后的记录列表，并清空快照缓存。
  - `snapshot(pinned)` 生成页面需要的完整快照；0.7 秒缓存用于去抖。
- `PetWindow`：Tkinter 无边框置顶小窗，每秒读取一次快照；支持拖动、打开网页、关闭服务。
- `Handler`：标准库 `ThreadingHTTPServer`，只绑定 `127.0.0.1`。

### `src/build_cn_token_dashboard.py`

- 扫描全量转录，生成近 7/30/90 天与全部历史的聚合。
- 输出：
  - `dist/Qoder_CN_Token用量统计.html`
  - `dist/cn_token_dashboard.html`
  - `dist/cn_token_dashboard.json`
  - `dist/Qoder_CN_Token用量统计.canvas.tsx`
  - `.qoder-token-usage/cn-token-dashboard.canvas.tsx`
- 静态页是生成时快照，不自动刷新。

## HTTP API

服务只监听 `127.0.0.1`，默认端口 8765；占用时自动尝试 8765–8784。

| 路径 | 说明 |
|---|---|
| `GET /` | 实时网页 |
| `GET /api/live?session=<id>` | 当前快照；`session` 为固定会话 ID，非法/不存在时回退到最近活跃会话 |
| `GET /api/ping` | `{"ok": true, "pid": ..., "title": ..., "version": ...}` |
| `GET /history` | 静态历史仪表盘；未生成时返回带命令说明的 HTML |
| `GET /favicon.ico` | 204 |

`/api/live` 主要字段：

| 字段 | 内容 |
|---|---|
| `generatedAt` / `generatedEpoch` | 快照生成时间（北京时间字符串 / Unix 秒） |
| `window` / `source` / `sourceExists` | 推导窗口、转录根、根目录是否存在 |
| `files` / `records` | 文件数、记录数 |
| `autoSession` / `selectedSession` / `followed` | 最近活跃会话、当前选中会话、是否自动跟随 |
| `session` | 会话元信息 + 汇总 + `rate1m` / `rate5m` |
| `today` | 今日汇总 + `topModels` / `topProjects` |
| `all` | 全量汇总 |
| `spark` | 最近 30 个一分钟桶 |
| `sessions` | 最近 12 个会话（含今日值、是否选中） |

## 口径与时间

- 时区固定为 UTC+8（北京时间），用于“今日”、日期分组和页面时间。
- Tokens = `context_usage_ratio × WINDOW` 逐条求和；这是计费输入总量的近似。
- Credits = `usage.credits` 逐条求和；`original_credits` 仅作对照。
- 免费/零扣费 = `credits <= 0`；Tokens 照常计入。
- 子代理记录来自 `<session>/subagents/*.jsonl`，计入总量并带 `sub: true` 标记。
- 输出/缓存 token 在 CN 桌面端转录中不存在，因此不展示。

## 隐私边界

- 扫描只读：`Path.rglob("*.jsonl")`，不写回转录。
- 运行状态与日志在 `dist/`：`.live_server.json`、`live_server.log`（滚动日志）。
- 生成物含本机项目路径、会话 ID 与消费数据；`.gitignore` 排除了 `dist/`、`.qoder-token-usage/`、旧 `build/` 与本地交接文档。
- 发布前只提交源码、模板、测试和文档；不要提交任何生成物或转录。

## 扩展建议

- 改口径：优先改 `cn_usage_core.py` 的 `parse_line` / `summarize` / `rows_for`，实时与静态会共享。
- 改实时页布局：`src/live_dashboard.html`。
- 改静态页/Canvas：`src/cn_token_template.html` / `src/cn_canvas_template.tsx`，然后重新生成。
- 新增 API：在 `Handler.do_GET` 中增加路由，保持只读与 `127.0.0.1` 绑定。
- 新增测试：`tests/` 使用标准库 `unittest`，不引入第三方依赖。

## 验证

```bash
python -m unittest discover -s tests -v
python src/build_cn_token_dashboard.py
python start.py --snapshot
```

测试覆盖解析、汇总、日期范围、增量 tail、半行写入、文件截断、子代理、快照和 HTTP API。
