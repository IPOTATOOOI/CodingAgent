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
```

Runner 使用 `.env` 或环境中的 `LLM_API_KEY`、`LLM_MODEL` 和可选
`LLM_BASE_URL`。它不会 Mock LLM，也不会并发执行任务。

每项结果记录 task ID、独立 verifier 成败、Agent stop reason、steps、tool calls、
verification status 和耗时。Verified Success Rate 只由独立 verifier 的退出码决定，
不使用 Agent 自我报告。结果写入 `eval/results/eval_*.json`，这些运行产物默认被 Git 忽略。

限制：任务集有意保持很小；LLM 行为具有非确定性；结果依赖模型和具体运行；这里只测量
选定的小型编码任务；测试通过不是程序正确性的形式化证明。
