# 香山 SPEC06 IPC 与性能计数器看板

基于原性能分析面板改造的无构建依赖单页看板，直接读取仓库中 `outputs/mainline-september` 的实测产物。

## 启动

必须通过 HTTP 服务打开，浏览器不能从 `file://` 跨目录读取 CSV：

```bash
cd /nfs/home/wujiabin/work/commit_ipc_trend
python3 tools/serve_dashboard.py --port 8000
```

访问：

```text
http://localhost:8000/xiangshan-performance-dashboard/
```

建议用 `tools/serve_dashboard.py` 而不是 `python3 -m http.server`：后者只发 `Last-Modified`，
浏览器会按启发式新鲜度长期复用缓存的 `index.html`/`app.js`，改完页面后仍然显示旧版本。
该脚本对所有响应加 `Cache-Control: no-store`。若之前用旧命令启动过，停掉它重新启动，
或先在浏览器里强制刷新一次（Ctrl+Shift+R / 无痕窗口）。

默认数据目录是 `../outputs/mainline-september`。也可用查询参数指向同源的其他兼容产物：

```text
http://localhost:8000/xiangshan-performance-dashboard/?data=../outputs/another-run
```

## 数据输入

页面读取以下文件，不内置分析结果：

- `manifest.json`：commit 元数据、观测数量和缺测清单
- `workload-weighted-trend.csv`：workload 加权 IPC、权重覆盖率
- `slice-ipc-wide.csv`：每个切片在各 commit 的 IPC
- `commit-transition-summary.csv`：相邻 commit 的切片升降分布
- `perf-counters.json`：由 SQLite 导出的注册计数器定义、mcf 切片和观测值
- `scores.csv`：数据库导出的 SPEC06 suite/benchmark 发布分数

默认数据由 `tools/build_mainline_september_dashboard.py` 和
`tools/import_mainline_counters.py` 从 SQLite 数据库生成。commit 必须位于
`origin/kunminghu-v3` 第一父链，并使用 `DefaultConfig`、固定 1094 个 SPEC06 gcc16
切片。计数器当前覆盖 5 个主线点的 18 个 mcf 高权重切片、20 个注册指标；筛选和导入
审计记录在 `outputs/mainline-september/selection-audit.json`。

## 分析口径

- workload IPC 沿用产物口径：`1 / weighted CPI`
- 套件 IPC 指数以首个 commit 为 100，取各 workload 相对 IPC 的几何平均
- A/B 提升、稳定、退化阈值为 IPC `±0.5%`
- 切片影响为 `weight * (CPI_B - CPI_A)`，正值代表性能拖累
- 缺测值显示为 `—`，不补零，也不参与可比数量与聚合
- 热力图中的 `*` 表示该 workload 在对应 commit 为部分权重覆盖

套件指数只表达这一批 workload 的整体相对变化，不是官方 SPEC score，也不直接平均不同 workload 的绝对 IPC。

## 功能

- 套件、workload、具体切片三级趋势下钻
- 绝对值 / 相对基线模式与任意 A/B commit 对比
- 55 个 workload 的 IPC 变化矩阵和 Top 12 贡献分析
- 1094 个切片的 A/B IPC、权重与加权 CPI 影响分析
- 相邻 commit 的提升 / 稳定 / 退化切片分布
- commit 元数据、覆盖状态和 A/B CSV 导出
- 桌面与移动端响应式布局
- 20 个核心性能计数器，按 Commit、Frontend、Backend、Cache、TLB/PTW 分类
- 指定切片的计数器趋势、方向一致的 A/B 性能变化和切片明细导出
- 计数器 × IPC 关系散点：切片水平 / A→B 变化 / 全区间面板三种视角，附 Spearman ρ 与最小二乘趋势
- 20 个计数器按 |ρ| 排序的相关性排行，点击即可切换趋势和散点对象
- 计数器 × 切片矩阵，按计数器方向着色，附各计数器的切片中位变化
- 原始计数 / 每千指令两种计数器口径切换（趋势、KPI、散点、矩阵、导出同步）

## 性能计数器数据链路

完整日志发现目录、注册清单、数据库和前端数据都由脚本生成。日志扫描只发生在离线导入阶段，页面不会重新扫描原始日志：

```bash
# 扫描一个或多个 run 下的切片日志，生成 path + name 级完整目录
python3 tools/discover_perf_counters.py /path/to/run-a /path/to/run-b \
  --glob 'mcf_*/simulator_err.txt' \
  --output /tmp/perf-counter-inventory.csv

# 导入主线点的 mcf 注册计数器并导出静态页面可读取的数据
python3 tools/import_mainline_counters.py

# 也可从任意包含 metric_definitions/counter_values 的数据库导出
python3 tools/export_dashboard_counters.py \
  --db outputs/mainline-september/mainline-performance.sqlite \
  --output outputs/mainline-september/perf-counters.json
```

计数器使用最后一次完整 PERF dump；完整性由前一 dump 的行数、首键和末键共同验证。
原始身份使用 `source_path + source_name`，因此不同模块的同名计数器不会混合。当前注册的
20 项在 5 个主线 run、18 个 mcf 切片的 90 份日志中均为唯一匹配且覆盖率为 100%。

计数器和 IPC summary 的窗口不同，因此当前只展示 raw count/raw cycles，不使用 IPC
窗口的 instructions 做归一化。相对模式会读取指标方向，将“越低越好”和“越高越好”
统一转换成正值代表性能改善。

## 计数器洞察口径

页面新增的“计数器 × IPC 关系”“计数器相关性排行”“计数器 × 切片矩阵”共用一套口径：

- **归一化分母**：使用计数器同窗口的 `rob_committed_instructions`。该窗口在所有切片和
  commit 上固定为 ~20M 指令（由 `test_exported_counters_share_a_normalizable_window`
  守住），所以“每千指令”口径可以跨切片直接比较，例如 `cycles/KInst`、`count/KInst`。
- **相关性**：对选定的计数器与切片 IPC 计算 Spearman ρ，三种视角分别是
  `切片水平`（同一 commit 下切片绝对水平的关系）、`A→B 变化`（所选区间内相对变化的关系）、
  `全区间面板`（相邻 commit × 切片的变化对汇总）。|ρ| ≥ 0.6 视为强、≥ 0.4 中等、≥ 0.2 弱。
- **方向**：矩阵和 A/B 判定读取指标的 `direction`，把“越低越好”和“越高越好”统一成
  绿色代表有利方向；单元格仍保留实际相对变化数值。
- **边界**：计数器来自 final PERF dump 窗口，IPC 来自切片 summary 窗口，两者窗口不同，
  因此 ρ 只用于诊断“哪些计数器跟着 IPC 一起动”，不构成因果归因；相关性强弱也不代表
  该计数器就是唯一瓶颈。
