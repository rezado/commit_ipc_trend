# 代码结构与维护入口

当前代码按离线导入、数据库查询、异常分析、rolling 分析和静态展示分工。
命令从仓库根目录执行；`outputs/` 保存生成数据和实跑证据。

## 核心模块

| 模块 | 职责 |
| --- | --- |
| `commit_ipc_trend/schema.py` | SQLite schema、版本和身份哈希 |
| `commit_ipc_trend/parsers.py` | manifest、score、ROI、PERF 日志解析与注册表读取 |
| `commit_ipc_trend/store.py` | 数据库连接、事务、幂等导入和版本校验 |
| `commit_ipc_trend/ingest.py` | PR 完成收据校验、checkpoint 解析和通用 manifest 构建 |
| `commit_ipc_trend/metrics.py` | CPI 聚合、方向性变化和切片贡献计算 |
| `commit_ipc_trend/anomalies.py` | 不访问数据库的切片候选排序与计数器配对 |
| `commit_ipc_trend/service.py` | 趋势查询、A/B 可比性、基线选择与异常报告组装 |
| `commit_ipc_trend/rolling.py` | 真实 rolling DB 检查、归档提取、分析任务编排与执行记录 |
| `commit_ipc_trend/metric_registry.json` | 指标身份、语义版本、单位、方向及日志匹配规则 |
| `commit_ipc_trend/counter_semantics.json` | 通用平台页面中的计数器与 Top-down 事件解释 |

`TrendService.detect_anomalies()` 保留公共入口：服务层读取数据库、选择基线、
执行 A/B 比较，再调用 `anomalies.py` 进行候选筛选和计数器配对。
纯计算模块不导入服务层，避免循环依赖。

## 命令入口

| 命令 | 用途与主要输出 |
| --- | --- |
| `tools/build_mainline_september_dashboard.py` | 固定九月数据集导入，生成 SQLite、CSV、manifest、筛选审计和异常 JSON |
| `tools/collect_spec06_slice_ipc_trends.py` | 构建脚本复用的切片采集、汇总与报表函数 |
| `tools/import_mainline_counters.py` | 补导计数器，刷新计数器与异常 JSON |
| `tools/export_dashboard_counters.py` | 从 SQLite 导出 `perf-counters.json` |
| `tools/export_dashboard_anomalies.py` | 从 SQLite 导出 `performance-anomalies.json` |
| `tools/query_demo.py` | 查询趋势、A/B 比较和异常报告；保留现有命令名称 |
| `tools/write_perf_receipt.py` / `tools/ingest_perf_run.py` | 写完成收据、校验并导入真实运行 |
| `tools/ingest_completed_runs.py` | 增量扫描已完成收据 |
| `tools/diagnose_perf_pair.py` | A/B 切片贡献与同窗口计数器证据 |
| `tools/analyze_perf_pair.py` | A/B Top-down / rolling 分析及数据库登记 |
| `tools/serve_perf_platform.py` | 通用数据库看板与第一父链基线 API |
| `tools/analyze_rolling.py` | rolling 的 `extract` / `run` 参数入口，调用包内实现 |
| `tools/discover_perf_counters.py` | 扫描日志中的指标，辅助维护注册表 |
| `tools/serve_dashboard.py` | 启动禁止浏览器缓存的静态 HTTP 服务 |

通用分析页面位于 `perf_platform_web/`，由 `serve_perf_platform.py` 提供静态文件和实时 SQLite API。
历史静态看板位于 `xiangshan-performance-dashboard/`，读取导出的 CSV/JSON；修改查询或分析逻辑后，需要重新导出相应数据，静态页面才会显示新结果。

## 修改与验证

- 修改 CPI、覆盖率或方向口径：检查 `metrics.py` 和 `tests/test_metrics.py`。
- 修改基线、可比性或报告结构：检查 `service.py`、`tests/test_store_service.py` 和 `tests/test_counter_pipeline.py`。
- 修改异常候选或计数器配对规则：检查 `anomalies.py` 和 `tests/test_anomalies.py`。
- 修改 rolling 校验、任务或产物：检查 `rolling.py` 和 `tests/test_rolling_analysis.py`。
- 修改日志解析或指标注册：检查 `parsers.py`、注册表和对应解析/计数器测试。

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

计数器必须按相同语义版本和统计窗口配对；缺失值不补零。
Rolling 使用仿真 ChiselDB，趋势 SQLite 不能作为其输入。
单切片 rolling 操作与口径见 [Rolling 分析流程](rolling-analysis.zh.md)。

PR #1 的三个提交已快进合入当前 `main`（`05606f0`），并整合了原工作区的异常分析和
单库 rolling。`outputs/pr-1-source/` 保留 PR 原始独立检出；当前维护入口是根目录下的
`commit_ipc_trend/` 和 `tools/`。完整流程见 [性能分析流程](perf-analysis-flow.zh.md)。
