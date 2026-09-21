# 切片性能趋势追踪落地方案

## 1. 结论

建议把当前仓库从“生成一张 mcf IPC 静态图的脚本”演进为一个**先离线导入、后交互查询**的单机 MVP：

```text
perf-report 原始目录
    │
    ├── run / score 解析
    ├── slice ROI 解析
    └── 指标白名单内的 PERF counter 解析
    │
    ▼
规范化清单 + SQLite
    │
    ├── 趋势查询
    ├── A/B 差分与贡献计算
    └── 可比性检查
    │
    ▼
Streamlit + Plotly 面板
```

第一阶段不需要立即部署 PostgreSQL、Grafana 或复杂前后端。SQLite 足以承载当前 CI 历史的规范化结果，Streamlit/Plotly 能较快验证筛选、联动、A/B 选择和下钻闭环。当前 Streamlit 的 Plotly 组件支持 point/box/lasso selection，Plotly 支持共享 x 轴子图，因而不需要自定义前端即可验证核心交互；实现时固定并测试依赖版本。数据契约、计算模块和查询接口与页面分离，规模或并发上升后再把存储/API 替换掉。

第一版的完成标准不是“图画出来”，而是用户可以完成：

> SPEC06 趋势发现变化 → benchmark/workload 定位 → slice 贡献排序 → 查看该 slice 的 IPC/CPI 和计数器趋势 → 回到原始日志。

## 2. 当前仓库与真实数据盘点

### 2.1 已有能力

当前数据库导入链路已经具备以下能力：

- 从 `cr<date>-<short_sha>-<Config>` 目录识别实际 RTL commit 和配置；
- 从本地 XiangShan Git 对象取得完整 SHA、committer time 和 subject；
- 从 `simulator_out.txt` 最后一个汇总读取 ROI 的 instructions、cycles、IPC；
- 对多个 run 取共同 mcf slice，并保留 checkpoint 权重；
- 产出 CSV、选择清单和静态趋势图，且有基本单元测试。

现有 7-run 示例可以继续作为第一版 golden dataset：

| 项目 | 现状 |
| --- | --- |
| run | 7 个 `DefaultConfig` run，2026-09-01 至 2026-09-18 |
| 完整切片规模 | 期望集合为 1094 个 slice；各 run 的发布结果可能部分缺失 |
| workload/input | 55 个；profile JSON 中每组权重和约为 1 |
| mcf | 18 个 slice；现有 demo 展示权重最高的 5 个，覆盖 70.61713% |
| SPEC 结果 | 每个 run 都有 `score-spec06-...-1.0c.txt` |
| 原始计数器 | `simulator_err.txt` 中的两次 `[PERF]` dump；最后一次对应 ROI 结束 |

### 2.2 可直接接入的源数据

| 来源 | 可提取字段 | 备注 |
| --- | --- | --- |
| run 目录名 | 日期、short SHA、config | short SHA 必须经 Git 扩展为完整 SHA |
| Git 对象 | full SHA、parent、committer time、subject | branch 只能作为运行标签，不能替代 SHA |
| `score-...-1.0c.txt` | SPEC06、SPECint/fp、benchmark score/time/coverage、checkpoint 版本、DRAM 配置、失败列表 | 保存评分文件名与评分器版本 |
| slice 目录名 | workload/input、checkpoint、weight | 应从右侧解析 `<name>_<checkpoint>_<weight>`，不能简单按第一个下划线切分；benchmark 需显式映射，不能从名称通用推断 |
| profile JSON | workload 总指令数、checkpoint、权重 | `/nfs/home/share/checkpoints_profiles/.../json/*.json` 是当前权重的权威来源；目录后缀仅用于交叉校验 |
| `simulator_out.txt` | seed、checkpoint image、CPU/DRAM 频率、instructions、cycles、IPC、运行状态 | CPI 由 `cycles / instructions` 重算，展示 IPC 也建议由原始整数重算并核对日志值 |
| `simulator_err.txt` | raw counter path、name、value、dump time | 一份样例有约 3.4 万个唯一 counter key，必须先做指标白名单 |

### 2.3 已确认的工程约束

- 单个完整 run 的 `simulator_err.txt` 合计约 10.7 GiB；7 个示例合计约 75 GiB。全量展开约 8200 万行/run，不能让页面请求时扫描原始日志，也不应在 MVP 中把所有 bucket 无差别写入普通长表。
- 单个样例 slice 有 warmup 结束和最终两次 PERF dump。日志明确显示 warmup 后 counter 会 dump 并 reset，因此注册计数器通常取**最后一组完整 dump 的绝对值**，不做前后相减；但 `simulator_out.txt` 的约 40M instructions/cycles 汇总与 PERF 最后一段约 30M cycles 的窗口并不天然一致。所有指标必须记录 `window_id`，counter 归一化只能使用同窗口的分母；拿不到同窗 instructions 时先展示 raw count 或标为 unsupported，不能用 40M summary 强行归一。
- 同一路径与名称有少量重复 key（例如直方图分段重复输出 `_sum` 等）。原始 counter 身份不能只用 `name`，至少要用 `path + name + occurrence/aggregation rule`。MVP 的注册指标只接受能唯一匹配的 key，或显式声明 `sum` 聚合规则。
- 7 个 run 的 mcf 样例中，raw counter key 的交集约占并集的 85.7%，说明硬件版本变化会导致计数器增删；缺失必须是 N/A，counter 语义版本边界不能被折线跨过。
- 当前 7 个点不是一条完整的 Git 祖先链。页面可以按 run/commit 时间显示“测试观测趋势”，但只有相邻点存在祖先关系且其他条件相同时，才能标成“连续提交趋势”；不能仅凭折线把变化归因到中间某个 commit。
- seed 是逐 slice 的执行属性，而不是 run 的单一属性，应保存在 `slice_results`。同一 run 中抽样 100 个 slice 已有 99 个不同 seed。
- 原始目录可能被续跑原地更新，评分文件也可能早于新生成的 slice 结果。导入只能消费完成标记后的不可变快照，并同时记录 score 生成时间、snapshot 时间和 retry lineage；否则同一 run 内 score 与 raw slice 状态可能矛盾。
- `perf-report` 中 run 命名不止当前 demo 的 9 位 SHA + `DefaultConfig`：已存在 10 位 SHA、多种 Config 和 `-dev`/`-nopf` 等后缀。通用 importer 不应复用当前窄 `DIR_RE` 作为完整契约，而应优先消费 CI manifest，目录名仅作兼容输入。

## 3. MVP 范围与非目标

### 3.1 MVP 必须完成

1. 幂等导入已有 `perf-report` run，并生成导入审计报告。
2. 展示 SPEC06、SPECint/fp、benchmark score 的 commit 趋势。
3. 展示 workload/input 的加权 CPI、等效 IPC 和覆盖率趋势。
4. 展示 slice 的 IPC、CPI、cycles 趋势。
5. 对注册表中的计数器提供共享 commit 横轴的联动小图。
6. 选择可比的 A/B 后，生成 workload 和 slice 的退化幅度、加权 CPI 贡献排名。
7. 从任意点跳转或复制原始日志路径，并展示完整 SHA、运行条件、解析器/公式版本和数据状态。

### 3.2 第一版明确不做

- 不做自动 CI 阻断和自动 culprit commit 定位；
- 不宣称一次单 seed 运行的变化具有统计显著性；
- 不全量入库 3 万多个 raw counter；
- 不把跨切片集或跨实验指纹的数据默认连接或比较；
- 不构造没有明确跨 workload 权重的“SPEC 平均 IPC”；
- 不在页面里在线重扫数 GiB 日志；
- 不先做多用户权限、自定义公式编辑器和复杂告警系统。

## 4. 数据契约

### 4.1 主键与身份

`commit_sha` 不是结果事实表的主键，`run_id` 才是。同一 SHA 可以因 seed、配置、环境或重跑而有多条 run。

建议 ID 规则如下：

- `run_id` 优先使用 CI 生成的稳定 UUID；兼容历史数据时用 `sha256(canonical source URI + immutable snapshot identity)`。同一路径后续续跑形成新 snapshot/retry，而不是覆盖旧结果；
- `slice_id = sha256(checkpoint_content_id + restore_mode + warmup + roi_definition)`；在暂时拿不到内容 hash 时，用规范化 checkpoint version、workload/input、checkpoint 和 ROI 定义生成 provisional ID，并显式标记；
- `slice_set_id = sha256(有序的 slice_id/weight 列表 + slice_set_schema_version)`；
- `comparison_key = sha256(canonical_json(硬可比字段))`；
- `metric_id` 和 `formula_version` 均不可原地覆盖，变更要新建版本。

### 4.2 核心表

SQLite 第一版建议使用下列表；字段可在迁移时增加，但这些语义先固定。

#### `commits`

```text
commit_sha PK, short_sha, commit_time, subject, parents_json
```

#### `runs`

```text
run_id PK, commit_sha FK, branch_label, config,
started_at, completed_at, score_generated_at, snapshot_at, imported_at,
source_uri, actions_url, retry_of_run_id,
status, parser_version, score_formula_version,
comparison_key, experiment_json
```

`experiment_json` 保存原始完整条件；常用筛选字段可同时提升为列。第一版硬可比字段至少包括：

```text
config
checkpoint/slice_set version
CPU frequency
DRAM frequency and DRAM config
warmup definition
ROI definition / instruction target and window ID
THP state if applicable
simulator/build semantic version
score formula version (score comparisons only)
```

branch、commit 时间、逐 slice seed、主机名、运行时间不进入默认硬签名，但都要展示。以后发现 seed 或主机对确定性有影响时，只需升级 `comparison_policy_version`。

#### `slices`、`slice_sets`、`slice_set_members`

```text
slices(slice_id PK, benchmark, workload, checkpoint, checkpoint_identity,
       restore_mode, warmup_definition, roi_definition)

slice_sets(slice_set_id PK, name, version, source_uri, created_at)

slice_set_members(slice_set_id FK, slice_id FK, weight,
                  weight_kind, ordinal, PRIMARY KEY(slice_set_id, slice_id))
```

这里将 identity 与 membership/weight 分开。当前目录名权重和约为 1，可作为 SimPoint 代表权重导入；在确认生成脚本的正式定义前，`weight_kind` 先记为 `simpoint_weight_unverified`，页面标注口径，不把它写成“指令比例”事实。

#### `slice_results`

```text
run_id FK, slice_id FK, status,
seed, window_id, instructions, cycles, ipc_reported, ipc_computed, cpi,
source_out_uri, source_err_uri, error_code,
PRIMARY KEY(run_id, slice_id)
```

状态枚举至少包含：

```text
valid, missing, partial, run_failed, parse_error, unsupported
```

`incomparable` 是 A/B 或序列关系，不是单条结果自身状态，应由比较层返回，避免混淆。

#### `metric_definitions`、`counter_values`

```text
metric_definitions(
  metric_id, semantic_version, display_name, category,
  kind, unit, direction, source_match_json,
  formula, aggregation_rule, valid_from, valid_to,
  PRIMARY KEY(metric_id, semantic_version)
)

counter_values(
  run_id, slice_id, metric_id, semantic_version,
  raw_value, numerator, denominator, value,
  availability, window_id, dump_time, source_uri,
  PRIMARY KEY(run_id, slice_id, metric_id, semantic_version)
)
```

`availability` 至少区分 `available / missing / unsupported / ambiguous / parse_error`。值为 0 且 available 与 N/A 是完全不同的状态。

#### `aggregate_results`

```text
run_id, level, object_id, metric_id, formula_version,
value, numerator, denominator, coverage_weight,
valid_member_count, total_member_count, status,
PRIMARY KEY(run_id, level, object_id, metric_id, formula_version)
```

`level` 取 `suite / benchmark / workload`。SPEC score 的“原始评分文件发布值”和按新公式重算值需要不同 `formula_version`，不能覆盖历史发布值。

#### `artifacts` 与 `import_batches`

```text
artifacts(run_id, slice_id NULL, kind, uri, content_hash NULL)
import_batches(batch_id, started_at, finished_at, parser_version,
               source_root, status, stats_json, errors_json)
```

用于追溯日志、评分文件、Actions 和导入失败。原始大文件只保留 URI，不复制进仓库或数据库。

### 4.3 导入清单

解析器先产出一个带 `schema_version` 的 JSON 清单，再事务性写 SQLite。这样 CI 将来可以直接上传清单，页面无需依赖共享 NFS 目录。最小形态：

```json
{
  "schema_version": "1.0",
  "parser_version": "commit-ipc-trend/0.1",
  "run": {
    "source_uri": "/nfs/home/cirunner/perf-report/cr260901-a355c5525-DefaultConfig",
    "commit_sha": "a355c5525a87981a89f50020ef4ddba8a29884df",
    "config": "DefaultConfig",
    "experiment": {}
  },
  "slice_set": {},
  "scores": [],
  "slice_results": [],
  "counter_values": [],
  "artifacts": []
}
```

导入必须支持 `--validate-only`，并按 `run_id` upsert。先写临时 batch，所有校验完成后再提交；部分 slice 失败可以形成 `partial` run，但不能伪装成完整 run。活跃目录标为 `in_progress`，评分文件早于 snapshot 且结果已变化时标为 `stale`，两者都不能进入默认趋势。发布状态至少区分 `in_progress / published / partial / stale / failed`。

## 5. 指标注册表与计算规则

### 5.1 性能指标

| metric | 层级 | 定义 | 越大/越小越好 |
| --- | --- | --- | --- |
| `ipc` | slice | `instructions / cycles` | 越大越好 |
| `cpi` | slice | `cycles / instructions` | 越小越好 |
| `cycles` | slice | ROI 原始 cycle count | 越小越好，但只在 instructions/ROI 一致时可比 |
| `weighted_cpi` | workload | `Σ(w_i × CPI_i)`，仅完整固定集合 | 越小越好 |
| `equivalent_ipc` | workload | `1 / weighted_cpi` | 越大越好 |
| `score` | benchmark | 沿用并版本化评分文件口径 | 越大越好 |
| `score_per_ghz` | suite | 沿用并版本化评分文件口径 | 越大越好 |

不要对 slice IPC 做加权平均。对缺失成员：

- 始终显示 `coverage_weight = Σ(valid w_i) / Σ(all w_i)`；
- `coverage == 1` 才产生正式的 `weighted_cpi`；浮点允许 `1e-5` 容差；
- 覆盖不全时正式值为 N/A，不静默重归一化；
- 页面可另选“共同有效切片”诊断模式。该模式对 A/B 共同成员重新归一化权重，必须显示 `common_coverage` 和 `diagnostic_common_subset` 标签，不能冒充完整 workload。

### 5.2 统一退化方向

所有归一化视图使用“正值表示退化”：

```text
higher_is_better: (base / current - 1) × 100%
lower_is_better:  (current / base - 1) × 100%
```

基线为 0 或 `abs(base) < metric.near_zero_threshold` 时，相对百分比返回 N/A，只显示绝对差。

### 5.3 A/B slice 贡献

只有 `comparison_key`、`slice_set_id`、权重和成员都一致，且 A/B 数据完整时，才输出严格可加的 CPI 贡献：

```text
contribution_i = w_i × (CPI_i,B - CPI_i,A)
Σ contribution_i = weighted_CPI_B - weighted_CPI_A
```

同时展示：

- slice 自身退化幅度，用于找最严重单点；
- CPI 加权贡献，用于找对 workload 总体影响最大者。

benchmark/SPEC score 是非线性聚合，MVP 只显示 A/B 差异并下钻，不伪造“slice 对 SPEC score 的精确贡献”。

### 5.4 counter 规则

第一版采用 YAML/JSON 指标注册表，只解析 10～30 个经过确认的计数器。例如可从当前 mcf 日志验证：

| metric | raw key/聚合 | 展示值 |
| --- | --- | --- |
| `ptw_fsm_requests` | 唯一匹配 `...ptw.ptw.ptw: ptw_fsm_req_count` | raw count；有同窗 instructions 后派生 `/MInst` |
| `ptw_mem_wait_cycles` | `...ptw.ptw.ptw: ptw_mem_wait_cycle` | raw cycles；有同窗 instructions 后派生 `/KInst` |
| `l2_demand_misses` | `...l2cache.topDown: l2demandMiss` | raw count；有同窗 instructions 后派生 `/KInst` |
| `l3_demand_dir_misses` | 对 `slices_*...mainPipe: l3demandReadDirMiss` 求和 | raw sum；有同窗 instructions 后派生 `/KInst` |

每个定义必须声明 exact/regex 匹配、期望匹配数、窗口、聚合方式、单位、方向和语义版本。匹配数不符合预期时标为 `ambiguous` 或 `missing`，禁止猜测并填 0。首批指标应优先保存 raw 分子/分母；只有窗口相同才生成归一化派生值。

workload 的 Events/KInst 要保留 raw event 与 instructions 后再聚合；miss rate 要分别汇总兼容语义的 miss 和 request 后相除，不能平均各 slice rate。

## 6. 可比性、横轴与重复运行

### 6.1 曲线何时连接

两个点仅在下列条件同时满足时连接：

- `comparison_key` 相同；
- 对该指标 `metric semantic_version` 相同；
- 数据状态 valid，正式聚合满足覆盖要求；
- 若选择“提交链模式”，前一个 commit 是后一个 commit 的祖先；若选择“运行时间模式”，允许非祖先但图例明确标为 CI 观测序列。

条件改变处画断线和边界标记。切片集变化只允许另开系列；“共同切片桥接”是显式诊断模式。

### 6.2 横轴

提供两种模式：

- 默认 `run_time`：按实际运行/导入时间或 Git committer time 排序，适合当前历史数据，标签为“CI 观测趋势”；
- `branch_first_parent`：用户选定 branch/ref 后，按 first-parent 顺序展示已测 commit，适合讨论提交链。

“相邻版本变化”指**同一 comparison key 下的上一个已测点**，不是 Git 中未测试的物理相邻 commit。

### 6.3 同 SHA 多 run

数据库永远保留每个 run。页面默认策略：

- 只有 1 次：显示该 run；
- 同 SHA、同 comparison key 多次：中心值显示 median，悬停显示 `n/min/max` 并允许展开每次 run；
- 不同 comparison key：绝不聚在一起；
- 第一版不计算置信区间，待有稳定复跑数据后增加噪声模型。

## 7. 服务与页面设计

### 7.1 模块边界

建议目录演进为：

```text
commit_ipc_trend/
  schema.py                 # 数据类、状态枚举、清单校验
  parsers/
    run_dir.py
    score_spec06.py
    slice_output.py
    perf_counter.py
  metrics.py                # 聚合、归一化、A/B 贡献
  comparability.py          # comparison key 与断线原因
  store.py                  # SQLite 事务、查询
  service.py                # 页面无关的趋势/对比接口
  metric_registry.yaml
tools/
  build_mainline_september_dashboard.py
  backfill_perf_reports.py
app.py
tests/
  fixtures/                 # 脱敏、裁剪后的日志 golden fixture
```

现有 demo 脚本先保留，逐步改为调用公共 parser/metrics，防止迁移时失去已验证结果。

### 7.2 页面最小交互

页面只设三块：

1. **筛选栏**：config、comparison key 摘要、slice set、时间、层级、对象、metric、显示模式。
2. **趋势区**：一张性能主图和若干 counter 小图，共享横轴、hover 和区间缩放；不可比边界断线。
3. **A/B 区**：点击两个点设 A/B，显示退化排序、贡献排序、覆盖率、状态和原始日志入口；点击一行下钻。

建议把筛选与 A/B 写入 URL query parameters，使一次诊断可以分享和复现。

### 7.3 查询接口

即使第一版直接在 Streamlit 中调用 Python，也先稳定以下服务函数边界：

```text
list_filters(...)
get_trend(level, object_ids, metric_id, filters, mode)
compare_points(run_or_group_a, run_or_group_b, level, object_id)
get_children(level, object_id, run_a, run_b, sort_by)
get_artifacts(run_id, slice_id=None)
```

返回值必须带 `status`、coverage、formula/semantic version、comparison key 和断线原因，不能只返回 x/y 数组。后续换成 FastAPI/HTTP 时页面无需重写计算逻辑。

## 8. 基于当前 mcf 示例的端到端验收

以现有 7 个 run 为 golden dataset，先完成下面这条纵向切片，而不是一开始导入 300+ 个历史目录的所有 counter。

### 8.1 示例路径

1. suite 图显示 7 个 run 各自发布评分 artifact 的 `SPEC2006/GHz`；例如首个 run 的评分文件值为 20.964。若源目录正在续跑或 artifact 已 stale，则保留历史发布点并标注，不将活目录状态覆盖进去。
2. 下钻到 `429.mcf`，首个 run 的 score 为 25.248。
3. 下钻到 mcf workload：导入全部 18 个 slice 得到正式加权 CPI；若只选当前 top-5，则页面必须显示 70.61713% coverage，并把结果标为部分集合诊断值。
4. 再下钻到 `mcf_6753_0.218742`，首个 run 应得到：
   - instructions = 40,000,004
   - cycles = 65,874,404
   - IPC 日志值 = 0.607216
   - CPI = cycles / instructions
5. 选 A=`a355c5525`、B=`017a698e0`：top-5 示例中每个 slice 的 `w × ΔCPI` 之和必须等于同一 top-5 口径的加权 CPI 差（允许 `1e-12` 数值误差）。
6. 联动显示 `ptw_fsm_requests`、`ptw_mem_wait_cycles` 和 `l2_demand_misses` 的同窗口 raw count，并能回到相应 `simulator_err.txt`；只有解析出同窗口 instructions 后才额外显示 `/MInst` 或 `/KInst`。
7. `df157ddd3 → 50b9c3387` 样例结果相同也应作为两个独立 run 保留；不能按数值去重。
8. 非祖先的相邻观测点在提交链模式下断开，运行时间模式明确显示为“CI 观测序列”。

### 8.2 自动化验收

- 同一目录导入两次，run/result/counter 行数不增加；
- 无效目录、活跃目录、stale score、缺失 Git SHA、重复/歧义 counter 匹配有明确错误或 batch 报告；
- `ipc_computed` 与日志 IPC 的误差超过阈值时标记 `parse_error` 或 warning；
- CPI/IPC、归一化退化方向、近零分母、coverage、共同子集和贡献和式均有单元测试；
- 不同 comparison key 自动断线，默认 A/B 比较返回 `incomparable` 和具体原因；
- counter 缺失为 N/A，真实 0 仍显示 0；
- 评分文件中的 suite/benchmark 数值与 golden fixture 精确一致；
- 任意趋势点都能查到完整 SHA、source URI、parser version 和 formula version；
- 7-run mcf 趋势查询在开发机冷启动后目标小于 2 秒，交互筛选目标小于 500 ms；页面查询期间不得扫描原始日志。

## 9. 实施阶段与任务拆分

以下按 1 名工程师估算；若数据接入和 UI 并行可缩短日历时间。

### Phase 0：冻结口径与 fixture（2～3 天）

- 从当前 7-run 示例的不可变快照裁剪一套可提交仓库的 score/out/err/profile fixture；
- 以 profile JSON 确认权重正式语义，并把 `weight_kind` 从 unverified 固化；建立显式 workload→benchmark 映射；
- 确认 PERF dump/reset 与窗口语义、同窗 instructions 来源和首批 10～30 个 counter 的负责人；
- 固化 manifest schema v1、状态枚举、comparison policy v1 和 metric registry v1；
- 记录当前 CSV/selection/评分值作为 golden output。

退出条件：字段来源、公式、缺失处理、硬可比字段都能由测试表达，不再依赖口头约定。

### Phase 1：规范化导入与 SQLite（4～6 天）

- 抽取通用 run/score/slice parser；
- 实现 manifest 校验、SQLite schema/migration、幂等事务导入；
- 导入 7 个完整 run 的 score 和全部 slice 核心指标；
- 只为 mcf 或用户指定对象解析 registry counter，避免第一次回填扫描所有 70+ GiB 日志；
- 生成 import batch 审计报告。

退出条件：第 8.2 节的数据正确性、幂等和追溯测试全部通过。

### Phase 2：计算与查询层（3～5 天）

- 实现 comparison key、曲线分段和 ancestor 标记；
- 实现 workload CPI/等效 IPC、coverage 和 suite/benchmark score 查询；
- 实现绝对值/固定基线/上一可比点模式；
- 实现 A/B slice 贡献、共同有效切片诊断模式；
- 建立必要索引和预聚合。

退出条件：用 7-run mcf 示例完整跑通 suite → benchmark → workload → slice → counter 的无 UI 查询。

### Phase 3：交互面板（4～6 天）

- 实现筛选、层级下钻、共享横轴图；
- 实现两点选择、差分/贡献表、coverage 与不可比原因；
- 实现 counter 搜索/分类和原始 artifact 入口；
- URL 保存筛选与 A/B 状态；
- 增加页面冒烟测试。

退出条件：用户可在页面独立复现第 8.1 节诊断路径，并能识别 N/A、断线和部分覆盖。

### Phase 4：CI 增量接入与历史回填（3～5 天）

- 回归结束后生成/上传 manifest，调用幂等 importer；
- 先回填 score/slice 核心指标，再按需回填 counter；
- 增加批量失败重试、导入监控、数据库备份和保留策略；
- 对 300+ 个历史 run 做 dry-run 审计，按 comparison key 分组后再逐批导入。

退出条件：新增一次回归无需改代码即可进入趋势，失败导入不污染已有数据。

### Phase 5：稳定性与告警（后续）

- 同 SHA 多次 run、同 slice 多 seed 的 median/分位区间与噪声基线；
- 异常提示、复跑工作流和 culprit commit 辅助定位；
- 团队级部署迁移到 PostgreSQL + FastAPI；
- 自定义冻结切片组、收藏指标组、CI 门禁。

## 10. 决策点与风险控制

开工前必须由数据/性能负责人确认三件事：

1. **权重语义**：profile JSON 中的 SimPoint 权重是否可严格作为 workload CPI 的代表指令权重。目录名只做对账；未确认前只做 slice 趋势和“部分集合诊断”，不发布正式等效 IPC。
2. **评分器版本**：`score-spec06` 的脚本/commit 需要可追溯；只记录文件名不足以支持历史重算。
3. **counter 语义 owner**：首批指标的 raw path、聚合规则和适用 RTL 范围需要负责人签字式确认，尤其是数组实例求和和硬件重命名边界。

其他主要风险及处理：

| 风险 | 处理 |
| --- | --- |
| 原始日志巨大，回填耗时 | 页面只读结构化库；按 registry 和对象分批回填；记录 batch checkpoint |
| 活目录续跑导致 score/raw 不一致 | 只导入完成标记后的不可变快照；记录 artifact/snapshot 时间与 retry lineage；stale 不进入默认趋势 |
| 目录名不能完整描述条件 | 从 out/score/CI manifest 取值；缺字段时 comparison key 标为 incomplete，禁止默认比较 |
| summary 与 PERF counter 窗口不同 | 所有值带 `window_id`；不同窗口只并排展示，不做相关归因或归一化 |
| slice 缺失造成“改善”假象 | 正式聚合不重归一化；显示 coverage；共同子集单独标注 |
| counter 改名或语义变化 | metric semantic version + valid range；未知版本为 unsupported |
| commit 时间顺序被误认为因果链 | 区分 CI 观测模式与 branch first-parent 模式；展示 ancestor 状态 |
| 单 seed 噪声被当成回归 | MVP 明示 n=1；保留 seed/run；后续以复跑分布建立阈值 |
| 修正解析器导致历史漂移 | 保留 parser/formula version 和原始 artifact；重算生成新版本，不覆盖旧结果 |

## 11. 推荐的第一张工程工单

第一张工单应是“**把 7-run mcf golden dataset 导入 SQLite，并提供无 UI 的 A/B 查询**”，而不是直接开发整页 UI。交付物包含：

- schema v1 和 metric registry v1；
- profile、score、slice out、3 个同窗口 raw counter 的解析器；
- 7-run 幂等导入命令与审计 JSON；
- `get_trend`、`compare_points` 两个服务函数；
- 本文第 8 节的自动化测试。

这张工单完成后，最难且最容易误导用户的三件事——数据契约、可比性和聚合口径——已经被代码与测试锁定；后续页面开发只是消费可信结果。
