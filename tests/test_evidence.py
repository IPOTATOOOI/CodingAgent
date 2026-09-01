"""结构化 Evidence Trail 的采集、脱敏、存储与回放数据测试。"""

import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from coding_agent.agent import AgentResult
from coding_agent.evidence import EvidenceStore, EvidenceTrailBuilder, MAX_DIFF_CHARS
from coding_agent.events import RuntimeEvent, RuntimeEventKind


class EvidenceTrailTests(unittest.TestCase):
    def test_builder_collects_changes_metrics_and_bounded_diff(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            builder = EvidenceTrailBuilder(workspace, "test-model", 20)
            builder.record(
                RuntimeEvent(
                    RuntimeEventKind.TASK_STARTED,
                    payload={"task": "Build an app", "max_steps": 20},
                )
            )
            content = "\n".join(f"line {index}" for index in range(2_000))
            builder.record(
                RuntimeEvent(
                    RuntimeEventKind.TOOL_STARTED,
                    step=1,
                    payload={
                        "tool_call_id": "write-1",
                        "tool_name": "write_file",
                        "arguments": json.dumps(
                            {"path": "src/app.js", "content": content}
                        ),
                    },
                )
            )
            builder.record(
                RuntimeEvent(
                    RuntimeEventKind.TOOL_FINISHED,
                    step=1,
                    payload={
                        "tool_call_id": "write-1",
                        "tool_name": "write_file",
                        "arguments": "{}",
                        "result": {
                            "success": True,
                            "data": {
                                "path": "src/app.js",
                                "created": True,
                                "bytes_written": len(content),
                            },
                        },
                        "verification_status": "unverified",
                    },
                )
            )

            snapshot = builder.finalize(
                AgentResult(
                    "Done",
                    "completed",
                    2,
                    tool_calls=1,
                    verification_status="verified",
                )
            )

        self.assertEqual(snapshot["task"], "Build an app")
        self.assertEqual(snapshot["files_created"], 1)
        self.assertEqual(snapshot["tool_calls"], 1)
        self.assertEqual(snapshot["verification"], "verified")
        self.assertEqual(
            snapshot["tools"][0]["arguments"]["content"],
            f"<{len(content)} chars omitted>",
        )
        self.assertLessEqual(len(snapshot["tools"][0]["diff"]), MAX_DIFF_CHARS)

    def test_store_round_trip_and_rejects_wrong_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = EvidenceStore(root / "traces")
            snapshot = {
                "version": 1,
                "trace_id": "trace-1",
                "task": "Test",
                "tools": [],
            }

            path = store.save(snapshot)
            loaded = store.load(path)

            self.assertEqual(loaded, snapshot)
            invalid = root / "invalid.json"
            invalid.write_text('{"version":99,"tools":[]}', encoding="utf-8")
            with self.assertRaises(ValueError):
                store.load(invalid)

    def test_automatic_store_cleanup_does_not_touch_exports(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            store = EvidenceStore(root / "traces", max_traces=2)
            paths = []
            now = time.time()
            for index in range(3):
                snapshot = {
                    "version": 1,
                    "trace_id": f"trace-{index}",
                    "tools": [],
                }
                paths.append(store.save(snapshot))
                os.utime(paths[-1], (now + index, now + index))
            exported = root / "shared.json"
            store.export({"version": 1, "trace_id": "shared", "tools": []}, exported)

            store.cleanup()

            self.assertEqual(len(list(store.root.glob("*.json"))), 2)
            self.assertTrue(exported.exists())


if __name__ == "__main__":
    unittest.main()
