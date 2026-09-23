# commit IPC trend

## 通用 perf-trigger 回归平台

按完成标记增量导入 perf-trigger 运行、查看 A/B 切片贡献、生成计数器证据摘要，以及调用 XiangShan top-down/rolling 的部署与命令，见 [docs/perf-platform.md](docs/perf-platform.md)。

这是一个基于 SQLite 的 XiangShan 性能趋势看板。系统将 CI 性能报告离线解析并导入数据库，前端只读取数据库导出的规范化数据，不在浏览器中扫描原始日志。

## 数据链路

```text
perf-report 原始目录
    ↓
build_mainline_september_dashboard.py
    ↓
mainline-performance.sqlite
    ↓
import_mainline_counters.py / export_dashboard_counters.py
    ↓
xiangshan-performance-dashboard/
```

数据库包含 commit、run、SPEC 分数、workload、slice IPC/CPI、加权 CPI、性能计数器和原始日志路径。每个 run 使用独立 `run_id`，并保存 parser、公式、实验条件、快照和可比性信息。

## 启动看板

```bash
python3 tools/serve_dashboard.py --port 8000
```

浏览器访问：

```text
http://localhost:8000/xiangshan-performance-dashboard/
```

默认数据目录为 `outputs/mainline-september/`，也可以通过 `?data=...` 指向同源的另一份兼容看板数据。

`tools/serve_dashboard.py` 与 `python3 -m http.server` 等价，但会对所有响应加
`Cache-Control: no-store`。`http.server` 只发 `Last-Modified`，浏览器会按启发式
新鲜度长期复用缓存的 `index.html`/`app.js`，改完看板后即使服务器已在提供新文件，
页面也可能一直显示旧版本。用这个脚本启动即可避免；如果已经在跑旧的 `http.server`，
先停掉它再启动，或者在浏览器里强制刷新一次（Ctrl+Shift+R / 无痕窗口）。

## 构建主线数据库

默认从 `/nfs/home/cirunner/perf-report` 中筛选 `origin/kunminghu-v3` 第一父链上的 `DefaultConfig` run，要求固定的 SPEC06 gcc16 RVA23 novec 切片集完整存在：

```bash
python3 tools/build_mainline_september_dashboard.py
```

产物位于 `outputs/mainline-september/`，主要包括：

- `mainline-performance.sqlite`：看板的规范化数据库
- `manifest.json`：数据范围和观测统计
- `selection-audit.json`：候选 run 筛选审计
- `scores.csv`、`workload-weighted-trend.csv`、`slice-ipc-wide.csv`：数据库导出的前端数据
- `perf-counters.json`：注册性能计数器数据

## 性能计数器

注册指标定义在 `commit_ipc_trend/metric_registry.json`。从数据库对应的原始 run 导入计数器：

```bash
python3 tools/import_mainline_counters.py
```

默认读取数据库记录的全部 run/slice 日志；当前主线数据覆盖 5 个 commit、55 个 workload、
1094 个切片和 20 个指标，共 109,400 条可用观测。导入器支持并行解析，也可做局部修复：

```bash
# 仅重新导入 mcf，或指定某个 commit / 切片
python3 tools/import_mainline_counters.py --workload mcf --jobs 8
python3 tools/import_mainline_counters.py --run c8d7b3a5c --slice-glob 'gcc_*'
```

也可以从任意包含 `metric_definitions` 和 `counter_values` 的数据库导出看板 JSON：

```bash
python3 tools/export_dashboard_counters.py \
  --db outputs/mainline-september/mainline-performance.sqlite \
  --output outputs/mainline-september/perf-counters.json
```

当前计数器取最后一次完整 PERF dump，使用 `source_path + source_name` 区分同名指标；缺失、歧义和截断数据不会被当成零值。

看板的性能计数器视图在“指定切片趋势 + A/B 明细”之上提供三类性能洞察：

- **计数器 × IPC 关系**：在当前 workload 内计算切片水平 / A→B 变化 / 全区间面板三种视角的散点，附 Spearman ρ
  与最小二乘趋势，用来判断哪些计数器真的和 IPC 同向；
- **计数器相关性排行**：20 个指标按 |ρ| 排序，点击即可切换趋势与散点对象；
- **计数器 × 切片矩阵**：按指标方向着色，展示每个计数器在各切片的相对变化和中位值。

计数器支持“原始计数”和“每千指令”两种口径。归一化分母取计数器同窗口的
`rob_committed_instructions`（在全部切片/commit 上固定为 ~20M 指令），因此逐指令口径
可以跨切片比较；具体口径与相关性边界见 `xiangshan-performance-dashboard/README.md`。

## 数据库查询

`commit_ipc_trend` 包提供 `Store` 和 `TrendService`。命令行查询工具支持趋势和 A/B 比较：

```bash
python3 tools/query_demo.py \
  --db outputs/mainline-september/mainline-performance.sqlite \
  trend --level workload --object mcf --metric equivalent_ipc

python3 tools/query_demo.py \
  --db outputs/mainline-september/mainline-performance.sqlite \
  compare --a <run-or-commit-a> --b <run-or-commit-b>
```

A/B 比较会检查 comparison key、slice set、发布状态和结果完整性，并返回覆盖率、切片 CPI 贡献和原始日志路径。

## 测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

测试覆盖 manifest 解析、IPC/CPI 计算、SQLite 幂等导入、counter 导入导出、趋势查询和看板 JavaScript 语法。

## 目录说明

- `commit_ipc_trend/`：解析器、指标计算、SQLite 存储和查询服务
- `tools/build_mainline_september_dashboard.py`：构建数据库及看板数据
- `tools/import_mainline_counters.py`：导入注册计数器
- `tools/export_dashboard_counters.py`：从数据库导出计数器 JSON
- `tools/query_demo.py`：数据库趋势/A-B 查询
- `tools/discover_perf_counters.py`：只读扫描日志以维护计数器注册表
- `xiangshan-performance-dashboard/`：数据库数据驱动的静态前端
- `docs/implementation-plan.md`：数据契约、表结构和后续演进方案

旧的直接扫描日志绘图脚本和 demo 产物已移除；数据库看板是当前唯一支持的展示流程。
