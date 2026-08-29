# Evaluation Framework

Stage 7 提供 6 个小型、顺序执行的真实 LLM Coding Agent 任务。

每个任务包含一个 `workspace/` fixture 和位于其外部的 `verify.py`。Runner 会把
fixture 复制到新的 `TemporaryDirectory()`，将复制后的目录作为 Agent workspace，
然后在 Agent 结束后由 Runner 单独执行 verifier。Agent 无法通过文件工具看到 verifier。

运行全部任务：

```bash
python eval/runner.py
```

运行单项或覆盖步数：

```bash
python eval/runner.py --task single_bug_fix --max-steps 12
python eval/runner.py --runs 5
python eval/runner.py --quiet-trace
```

Runner 使用 `.env` 或环境中的 `LLM_API_KEY`、`LLM_MODEL` 和可选
`LLM_BASE_URL`。它不会 Mock LLM，也不会并发执行任务。

每项结果记录 task ID、独立 verifier 成败、Agent stop reason、steps、tool calls、
verification status 和耗时。Verified Success Rate 只由独立 verifier 的退出码决定，
不使用 Agent 自我报告。结果写入 `eval/results/eval_*.json`，这些运行产物默认被 Git 忽略。

Runner 默认输出不含文件内容和命令输出的逐步轨迹。每个任务结束后都会通过同目录临时文件
原子更新 checkpoint；完整运行结束后 `complete=true`。`--runs` 会顺序重复整套任务并
统计每任务 pass rate、整套运行成功率、Agent completion/max-steps rate，以及 steps、
tool calls、duration 的 p50/p95。

结果元数据包含模型名、Agent 版本、Git commit、Python/平台、max steps 和 run count，
但不保存 API Key 或 Base URL 的具体值。Agent 结束后才会把预先读入内存的 verifier
写入新的临时目录执行，并检查原始 fixture/verifier 完整性。这不是 OS 级 Sandbox。

Completion Gate 的 A～D 真实场景可以单独执行，并逐项保存 checkpoint：

```bash
python eval/verification_cases.py --max-steps 12
python eval/verification_cases.py --case B --max-steps 12
```

限制：任务集有意保持很小；LLM 行为具有非确定性；结果依赖模型和具体运行；这里只测量
选定的小型编码任务；测试通过不是程序正确性的形式化证明。
