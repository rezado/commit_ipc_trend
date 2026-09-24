# Rolling 性能分析流程

从趋势定位 workload，按 `weight × ΔCPI` 选择重点切片，查看末次 PERF dump 的计数器线索，再用 rolling 定位低 IPC 发生的阶段。最后回到 RTL 事件定义核实解释。

```text
趋势 / A-B → 切片 CPI 贡献 → 同窗口计数器 → Rolling DB
                                         ↓
                              表清单 / 周期校验 / 图 / 相关性
                                         ↓
                                 证据与限制 / RTL 核对
```

## 1. 定位数据

Rolling 输入是仿真生成的 ChiselDB，通常名为 `simulator.db`，含 `ipc_rolling_0`、`td_cycle_*_rolling_0` 等表。`mainline-performance.sqlite` 是趋势库，不能用它替代 rolling 输入。

先根据 Actions run 核对实际 checkout SHA、benchmark、checkpoint、配置和运行条件，再查找对应 run 目录。共享目录可能已将历史 run 压缩为 `.tar.zst`；没有解压 DB 不等于没有数据，抽查几个无关归档也不能证明所有 run 都没有 DB。网络命令通过 `proxychains`：

```bash
proxychains curl -fLsS \
  https://api.github.com/repos/OpenXiangShan/XiangShan/actions/runs/30823230496
find /nfs/home/cirunner/perf-report -maxdepth 1 -name '*4ce2f88ae*'
```

本次实测源归档为 `cr260731-4ce2f88ae-DefaultConfig.tar.zst`。只提取所需成员，避免展开整个约 91.5 GB 的压缩包：

```bash
python3 tools/analyze_rolling.py extract \
  --archive /nfs/home/cirunner/perf-report/cr260731-4ce2f88ae-DefaultConfig.tar.zst \
  --member cr260731-4ce2f88ae-DefaultConfig/xalancbmk_24536_0.0162659/simulator.db \
  --output outputs/rolling-example/simulator.db
```

依赖系统 `tar`、解压 `.zst` 时还需 `zstd`。该命令流式读取一个精确成员，不恢复归档中的目录和链接，不覆盖已有文件；验证 SQLite 文件头后才发布结果。空间应按未压缩 DB 估算，本例约 4.2 GB。也需提取/核对同目录 `simulator_out.txt` 的完成状态、RTL SHA 和 checkpoint 身份。`--source-run` 仅保存出处，不自动证明 DB 来自该 run。

## 2. 执行单切片分析

包装脚本只用 Python 标准库；实际算法复用 XiangShan `scripts/rolling.py`，不另写相关性或 rolling 引擎。给 `--python` 指定安装了 numpy、matplotlib 等上游依赖的解释器：

```bash
python3 -m venv .venv-rolling
proxychains .venv-rolling/bin/python -m pip install \
  -r /path/to/XiangShan/scripts/requirements.txt

python3 tools/analyze_rolling.py run \
  --db outputs/rolling-example/simulator.db \
  --xiangshan /path/to/XiangShan \
  --python .venv-rolling/bin/python \
  --hart 0 --aggregate 50 \
  --perf-name ipc --perf-name td_cycle_LoadMemStall \
  --prefetch-progress \
  --source-run https://github.com/OpenXiangShan/XiangShan/actions/runs/30823230496 \
  --output-dir outputs/rolling-example/analysis
```

默认只画 IPC；重复 `--perf-name` 增加已确认同周期坐标的指标。每次使用新的分析目录，避免旧图与新记录混淆。数据库只读预检，不存在的路径不会创建空 SQLite。底层上游工具也应只对已经完成、停止写入的数据库运行。

| 产物 | 内容 |
| --- | --- |
| `analysis.json` | 输入大小/mtime、表名、窗口范围、条件、出处、工具 hash、命令与返回码 |
| `list.stdout.txt` | hart 下各表的样本数、坐标和值范围 |
| `rolling.png` | 聚合后的 IPC 和所选同周期计数器 |
| `correlation-cycle.csv` | 同周期窗口精确对齐的 Pearson 相关性 |
| `correlation-prefetch-progress.csv` | 可选的预取阶段插值相关性 |
| `*.stdout.txt` / `*.stderr.txt` | 每个阶段的执行日志 |

`status=completed` 表示所选步骤完成；`partial` 表示部分步骤被跳过，检查 `skipped`；`failed` 表示工具出错、超时、产物缺失或分析中源文件发生变化。空表/恒定序列不能计算相关性时记录跳过，不把它当零相关。默认单个命令超时为 3600 秒，可用 `--timeout` 调整。

## 3. 解释窗口与结果

- IPC 周期坐标必须递增；重置/重复坐标会拒绝分析。周期域 raw count 只有在固定长度窗口下才能解释为与事件率成比例。包装器检查 IPC 从零起的窗口及各周期事件坐标；可变窗口或未知起点跳过图和 raw-count 相关性，不能强行套用固定窗口口径。
- `--aggregate 50` 是合并 50 个记录。本例每记录 1000 周期，因此图中每点为 50000 周期；相关性仍在原始 1000 周期窗口计算。
- Rolling 可能包含 warmup，末次 PERF dump 往往只包含 reset 后测量区间。窗口内指令合计与日志完整运行的末尾差异也要核对，不能混用分母。
- 预取指标使用 `progress` 插值只作阶段共变诊断，默认不开启。它不是周期精确对齐，也不自动将计数变成准确率/覆盖率。
- 相关性不是因果；NoStall 的强正相关符合定义，stall 指标正相关可能是高吞吐阶段的伴随事件。结合高 CPI 贡献切片、原始值和 RTL 定义再下结论。
- 单切片不代表整个 workload；不同编译器/checkpoint 的曲线不能当相同程序阶段的 A/B。

## 4. 与 PR 的 A/B 流程衔接

PR #1（05606f0）已合入当前主工作区，原有趋势/异常分析改动也已整合。
根目录的 `docs/perf-analysis-flow.zh.md` 描述收据、导入、可比基线、贡献及 Top-down 流程；
`outputs/pr-1-source` 保留 PR 原始独立检出。

本仓库 `tools/analyze_rolling.py` 补充只有一个真实 DB 时的清单/校验/相关性/绘图入口。
两边都有同一 checkpoint 的 DB 时，用 `tools/analyze_perf_pair.py ... rolling --base-db ... --target-db ... --perf-name ipc`
生成 A/B 叠加并登记 `analysis_runs`。包装器校验趋势库 A/B 可比性、两份 DB 的 IPC 表与坐标，
并拒绝两边使用同一文件；两边 DB 的 run、slice 和实验归属仍需核实。

没有 DB 时流程停留在切片/计数器/Top-down，并记录缺口；聚合日志不能重建真实 rolling 时间线。

## 5. 已验证实例

Actions 30823230496，GCC15 xalancbmk checkpoint 24536、权重 1.62659%，实际 RTL `4ce2f88ae7`。数据库含 211 张 rolling 表、9740 个 IPC 窗口，每窗 1000 周期；rolling IPC 为 4.106623，原日志完整运行 IPC 为 4.106587。末尾不足一窗解释了小差异。

该切片 IPC 与 `LoadMemStall` 的 Pearson r 约 -0.440，与 `ControlRedirectStall` 约 -0.424，与 `TAGEMissBubble` 约 -0.421，均只是定位线索。本地原始报告见 `outputs/rolling-30823230496/REPORT.zh.md`。这不是九月 GCC16 回归的 A/B 结论。
