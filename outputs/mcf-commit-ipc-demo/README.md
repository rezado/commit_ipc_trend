# mcf commit IPC trend demo

这份 demo 从 `/nfs/home/cirunner/perf-report` 中挑选 7 个
`cr2609*-DefaultConfig` 提交测试，比较它们共同存在的 `mcf` SimPoint 切片。

## 产物

- `mcf-ipc-trend.png`：每个切片一个独立纵轴的 IPC 趋势图；横轴按 Git committer time 排序。
- `mcf-ipc-trend.csv`：35 条原始观测（7 commits × 5 slices），保留完整 SHA、提交时间、
  subject、instructions、cycles、IPC 和源文件路径。
- `selection.json`：目录、切片、排序规则和 Git 元数据，便于审计本次筛选。

## 筛选口径

- 只比较 `DefaultConfig`，不混入 `KunminghuV2Config`。
- 7 个目录跨 2026-09-01 至 2026-09-18；每个目录都有 18 个相同且带有效 IPC 汇总的
  `mcf` 切片。
- 在 18 个严格交集切片中选择权重最高的 5 个：checkpoint
  `6753`、`12886`、`11326`、`5365`、`9639`，权重合计 `0.7061713`。
- IPC 读取 `simulator_out.txt` 最后一个
  `instrCnt = ..., cycleCnt = ..., IPC = ...` 汇总，不把目录名末尾的权重误作 IPC。
- 横轴按 Git committer epoch `%ct` 排序；目录名中的日期本身不足以确定同日先后顺序。

图中每个面板独立缩放纵轴，便于看到同一切片在提交间的细微变化；面板之间的线条高度
不能直接比较，绝对 IPC 请看纵轴或 CSV。首尾比较显示 5 个切片分别变化
`+4.30%`、`+0.78%`、`+2.32%`、`+2.48%`、`+4.11%`。主要跃升集中在
`a355c5525 → 017a698e0`，之后整体相对平稳，其中 checkpoint `12886` 在后段略有回落。

这些是按提交时间排列的 CI 测试样本，不保证每个相邻点都处于同一条 Git 祖先链，因此
适合表达“提交测试趋势”，不能仅凭折线把变化归因于某一个相邻 commit。

## 复现

在仓库根目录运行：

```bash
python3 tools/plot_mcf_commit_ipc_demo.py
```

脚本也支持重复传入 `--commit-dir` 来换一组目录，以及用 `--slice-count` 调整切片数量。
