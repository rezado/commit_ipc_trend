# XiangShan performance regression platform (MVP)

This implementation uses the existing `commit_ipc_trend` SQLite schema and query service. It adds a general perf-trigger importer, a read-only dashboard/API, an evidence summary and optional top-down/rolling adapters. It does not run benchmarks or modify XiangShan RTL.

## Deploy on a host with the perf-report NFS mount

Requirements: Python 3.10+, a local XiangShan Git checkout containing tested commits, access to the perf-report directory and to the checkpoint profile JSON actually used by CI. The Python importer and dashboard use only the standard library; the XiangShan analysis scripts have their own dependencies. One DB writer should run at a time; the read-only dashboard may serve concurrently.

1. For each **completed** `perf-trigger` run, write `<SPEC_DIR>/.perf-platform-receipt.json` **after** the score/report step succeeds. Never write it for an in-progress or failed workflow. The following is an example for a SPEC06 run; set actual values from the workflow and simulator, including frequency and build options:

```bash
python3 tools/write_perf_receipt.py \
  --report-dir "$SPEC_DIR" \
  --checkpoint-list "$CKPT_JSON_PATH" \
  --score-file "$SCORE_FILE" \
  --commit-sha "$(git rev-parse HEAD)" \
  --actions-run-id "$GITHUB_RUN_ID" \
  --branch-label "$GITHUB_REF_NAME" \
  --config "$XS_CONFIG" \
  --emulator "$EMULATOR" \
  --benchmark-type "$BENCHMARK_TYPE" \
  --checkpoint-identity "$CKPT_HOME" \
  --warmup "$ACTUAL_WARMUP" \
  --max-instr "$ACTUAL_MAX_INSTR" \
  --max-cycles "$ACTUAL_MAX_CYCLES" \
  --dram-config "$ACTUAL_DRAM_CONFIG" \
  --cpu-frequency-mhz "$ACTUAL_CPU_FREQ" \
  --dram-frequency-mhz "$ACTUAL_DRAM_FREQ" \
  --build-options "$BUILD_OPTIONS" \
  --score-formula-version score-spec06/published-v1 \
  --output "$SPEC_DIR/.perf-platform-receipt.json"
```

`XS_CONFIG`, `EMULATOR`, `BENCHMARK_TYPE`, `ACTUAL_*` and `BUILD_OPTIONS` above are placeholders: perf-template currently does not expose all of them as environment variables. The workflow integration must wire the **resolved actual settings**, including defaults, rather than copying the example literally. For custom benchmarks without a score file, omit `--score-file` and use an explicit formula identifier such as `none`. For `benchmark_map`, add an optional mapping object to the receipt, e.g. `{"mcf":"429.mcf"}`. The importer accepts the checkpoint profile JSON (`{"workload":{"points":{"checkpoint":"weight"}}}`) or the existing one-slice-per-line text format. The JSON used by CI is preferred. Profile weights must be positive and sum to at most one for each workload. A 0.3c subset is displayed as partial coverage, with formal workload CPI reported as N/A.

2. Validate the first run, then import it:

```bash
python3 tools/ingest_perf_run.py --receipt "$SPEC_DIR/.perf-platform-receipt.json" \
  --git-repo /path/to/XiangShan --db /srv/perf/trend.sqlite \
  --manifest-out /srv/perf/manifests/run.json --validate-only
python3 tools/ingest_perf_run.py --receipt "$SPEC_DIR/.perf-platform-receipt.json" \
  --git-repo /path/to/XiangShan --db /srv/perf/trend.sqlite
```

The first pass leaves the large PERF logs alone. Use `--counters` for selected runs when registered metrics are needed. A repeated import with identical file metadata uses the same run ID; a modified source snapshot gets a new run ID. Import only after a final receipt, as an in-place retried report can otherwise mix score and slice generations. Score files older than their slice ROI summaries are marked stale and excluded from published trends.

3. Run a poller periodically (for example, via systemd timer) to import all new final receipts. A repeated poll skips existing run IDs:

```bash
python3 tools/ingest_completed_runs.py \
  --report-root /nfs/home/cirunner/perf-report \
  --git-repo /path/to/XiangShan --db /srv/perf/trend.sqlite
```

4. Serve the dashboard on loopback, then access through your team's authenticated reverse proxy if needed:

```bash
python3 tools/serve_perf_platform.py --db /srv/perf/trend.sqlite --host 127.0.0.1 --port 8000
```

The page supports selecting two runs and a workload, shows weighted CPI contributions, data coverage and source paths. JSON endpoints: `/api/runs`, `/api/workloads?run=...`, `/api/compare?a=...&b=...&workload=...`, `/api/trend?level=workload&object=mcf&metric=equivalent_ipc`, `/api/artifacts?run=...`. The existing richer static trend dashboard remains available through `tools/serve_dashboard.py` for the historical mainline export; the new page reads the live SQLite DB.

## Investigate a regression

```bash
python3 tools/query_demo.py --db /srv/perf/trend.sqlite compare \
  --a <base-run-id> --b <target-run-id> --object mcf
python3 tools/diagnose_perf_pair.py --db /srv/perf/trend.sqlite \
  --a <base-run-id> --b <target-run-id> --workload mcf \
  --output /srv/perf/cases/mcf/diagnosis.json
```

The diagnosis ranks positive `weight × ΔCPI` slices and shows pairs of available registered counters with identical semantic versions and window IDs. A same-direction counter across multiple slices is only a lead; the script does not claim causal RTL attribution or normalize a PERF dump using a different ROI window.

Optional adapters execute upstream scripts in an isolated output directory and record inputs, tool hashes and output paths in `analysis_runs`:

```bash
python3 tools/analyze_perf_pair.py --db /srv/perf/trend.sqlite \
  --a <base-run-id> --b <target-run-id> --workload mcf \
  --xiangshan /path/to/XiangShan --output-dir /srv/perf/analyses \
  topdown --checkpoint-json /path/to/checkpoints.json \
  --base-issue 8 --target-issue 8

python3 tools/analyze_perf_pair.py --db /srv/perf/trend.sqlite \
  --a <base-run-id> --b <target-run-id> --workload mcf \
  --xiangshan /path/to/XiangShan --output-dir /srv/perf/analyses \
  rolling --base-db /path/to/base.db --target-db /path/to/target.db \
  --perf-name ipc --hart 0
```

Top-down consumes complete run report directories and its checkpoint JSON. Verify its script/config counter definitions against the RTL under analysis. Rolling requires ChiselDB produced by `enable_rolling`; it cannot reconstruct a missing time series. Its `diff` subcommand overlays two curves and does **not** align program semantics across changed binaries.

## Data contract and known limits

- Runs are keyed by immutable `run_id`; comparison keys exclude commit but include full experiment settings, checkpoint set and content hash. Incomplete, stale and missing-slice runs are not strict baselines.
- Workload CPI is `Σ(weight × slice CPI)`, with formal output only at full coverage. `weight × ΔCPI` contributions sum to workload CPI difference. No slice IPC averaging or fabricated SPEC-score contributions.
- The imported score parser currently supports SPEC06 published score text. Other suite score formats need dedicated parser adapters; their workload/slice CPI can still be imported.
- The receipt writer is available, but XiangShan's upstream `perf-template.yml` is **not modified in this branch**. Automatic ingestion starts when CI writes the receipt with actual resolved settings, or when an operator writes it after verifying a completed run.
- The platform offers evidence-backed counter patterns. Stable RTL event mappings, alert thresholds, repeated-run noise models and real program-phase alignment are future work and require data calibration.
