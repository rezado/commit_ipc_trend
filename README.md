# commit IPC trend

这是一个基于 SQLite 的 XiangShan 性能趋势与回归分析工具。CI 性能报告由 Python 离线解析并导入数据库；页面读取数据库或从数据库导出的规范化数据，不在浏览器中扫描原始日志。

## 通用 perf-trigger 回归平台

增量导入已完成的 perf-trigger 运行后，启动读取实时 SQLite 的分析页面：

```bash
python3 tools/serve_perf_platform.py --db /srv/perf/trend.sqlite \
  --git-repo /path/to/XiangShan --host 127.0.0.1 --port 8000
```

页面按提交趋势、整体分数、workload、切片、计数器和已登记的 Top-down/Rolling 产物逐层查看。`--git-repo` 用于选择第一父链上的可比已测试基线。收据、导入、服务部署与 API 见 [通用平台说明](docs/perf-platform.md)；具体排查命令见 [性能分析 Flow](docs/perf-analysis-flow.zh.md)。

下文的 `xiangshan-performance-dashboard/` 是从固定主线数据导出的静态看板，使用另一套启动入口。

## 数据链路

```text
perf-report 原始目录
    ↓
build_mainline_september_dashboard.py
    ↓
mainline-performance.sqlite
    ↓
import_mainline_counters.py / export_dashboard_counters.py / export_dashboard_anomalies.py
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
- `performance-anomalies.json`：最新点相对最近可比点和固定基线的异常清单

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

### 异常性能数据识别（无需复跑）

`anomalies` 直接分析数据库中的现有观测，不会启动仿真或创建复跑任务。未指定
`--baseline` 时，优先选择已测试的直接父 commit；父 commit 没有观测时，选择最近的
可比历史 run。还可以同时指定固定验收基线：

```bash
python3 tools/query_demo.py \
  --db outputs/mainline-september/mainline-performance.sqlite \
  anomalies --current <new-run-or-commit> \
  --git-repo /path/to/XiangShan \
  --fixed-baseline <release-run-or-commit>
```

更新静态看板中的异常清单：

```bash
python3 tools/export_dashboard_anomalies.py
```

提供 `--git-repo` 时，异常查询和导出使用 PR 的第一父链祖先基线策略；未提供时保留历史兼容选择方式。

默认报告全部 workload，也可以用多个 `--workload` 缩小范围。常用策略参数为：

```text
--workload-threshold-pct 0.5       workload 加权 CPI 退化阈值
--slice-threshold-pct 0.5          单 slice CPI 退化阈值
--contribution-top-n 10            异常 workload 的正向加权 ΔCPI 候选数
--counter-change-threshold-pct 5   计数器方向性变化阈值
--counter-top-n 5                  每个异常 slice 返回的计数器线索数
```

输出同时保留 workload 加权 CPI、单 slice 退化幅度、`weight × ΔCPI`、异常原因、
原始日志路径和同窗口计数器变化。计数器只用于提供定位线索，不作为因果结论。
报告中的 `statistical_significance=not_assessed_single_observation` 明确表示它是单次观测的
实际影响筛选，不声称统计显著性。

## Rolling 阶段分析

在 workload/slice CPI 贡献和 PERF 计数器定位之后，使用真实 ChiselDB 进一步分析发生阶段。
正式入口为 `tools/analyze_rolling.py`，支持从 `.tar.zst` 中精确提取一个 DB，以及单切片
rolling 表校验、IPC 时序图、同周期相关性和可选预取阶段相关性。命令、统计窗口、工具版本及
执行状态统一保存到 `analysis.json`，不会将趋势 SQLite 当作 rolling 输入。

完整操作和已验证的 Actions 30823230496 示例见 [Rolling 分析流程](docs/rolling-analysis.zh.md)。
PR #1 已合入当前主工作区；A/B 包装器为 `tools/analyze_perf_pair.py`，与单库入口的适用边界见上述文档。
整合后实跑证据位于 `outputs/pr-integrated-20260924/`。

## 测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

测试覆盖 manifest 解析、IPC/CPI 计算、SQLite 幂等导入、counter 导入导出、趋势查询和看板 JavaScript 语法。

## 目录说明

模块职责、命令入口和测试对应关系见 [代码结构与维护入口](docs/code-structure.zh.md)。

- `commit_ipc_trend/`：解析器、指标计算、SQLite 存储和查询服务
- `commit_ipc_trend/anomalies.py`：异常切片候选筛选和同窗口计数器配对
- `commit_ipc_trend/counter_semantics.json`：通用平台展示的计数器与 Top-down 事件解释
- `commit_ipc_trend/rolling.py`：rolling DB 校验、任务编排和执行记录
- `tools/build_mainline_september_dashboard.py`：构建数据库及看板数据
- `tools/import_mainline_counters.py`：导入注册计数器
- `tools/export_dashboard_counters.py`：从数据库导出计数器 JSON
- `tools/export_dashboard_anomalies.py`：从现有数据库导出异常清单 JSON
- `tools/query_demo.py`：数据库趋势/A-B 查询
- `tools/analyze_rolling.py`：归档 DB 提取、单切片 rolling 校验/相关性/绘图
- `tools/serve_perf_platform.py` 与 `perf_platform_web/`：实时 SQLite 的分析 API 与页面
- `docs/rolling-analysis.zh.md`：从切片贡献到 rolling 的操作流程和实测案例
- `tools/discover_perf_counters.py`：只读扫描日志以维护计数器注册表
- `xiangshan-performance-dashboard/`：数据库数据驱动的静态前端
- `docs/implementation-plan.md`：数据契约、表结构和后续演进方案

旧的直接扫描日志绘图脚本和 demo 产物已移除；数据库看板是当前唯一支持的展示流程。
