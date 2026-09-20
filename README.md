# commit IPC trend

从 XiangShan CI 性能报告中提取同一 workload、同一 SimPoint 切片在不同提交下的
IPC，并按 Git committer time 绘制趋势图。

当前 demo 使用 `/nfs/home/cirunner/perf-report` 下 7 个
`cr2609*-DefaultConfig` 目录，展示它们共同存在的 5 个高权重 `mcf` 切片。

## Demo 结果

- [趋势图](outputs/mcf-commit-ipc-demo/mcf-ipc-trend.png)
- [IPC 明细](outputs/mcf-commit-ipc-demo/mcf-ipc-trend.csv)
- [筛选清单和 commit 元数据](outputs/mcf-commit-ipc-demo/selection.json)
- [口径与初步结论](outputs/mcf-commit-ipc-demo/README.md)

## 运行

需要 Python 3.10 或更新版本，以及 Pillow：

```bash
python3 -m pip install -r requirements.txt
python3 tools/plot_mcf_commit_ipc_demo.py
```

默认会重新扫描原始报告并覆盖 `outputs/mcf-commit-ipc-demo/` 中的 PNG、CSV 和
JSON。可用重复的 `--commit-dir` 替换默认提交目录，用 `--slice-count` 调整展示的
共同切片数：

```bash
python3 tools/plot_mcf_commit_ipc_demo.py \
  --commit-dir cr260901-a355c5525-DefaultConfig \
  --commit-dir cr260918-575aef181-DefaultConfig \
  --slice-count 2
```

横轴时间来自本机 XiangShan Git 对象；脚本会查找 CI runner 工作树，也可通过
`--git-repo /path/to/XiangShan` 显式指定仓库。

## 测试

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
```

原始性能报告只读使用，不会被脚本修改或复制到本仓库。
