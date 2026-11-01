import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).parents[1]


class CliEncodingTests(unittest.TestCase):
    def invoke(self, request, folder, *extra):
        source, output = folder / "input.json", folder / "output.json"
        source.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8-sig")
        env = dict(os.environ, PYTHONPATH=str(ROOT / "src"), PYTHONIOENCODING="gbk")
        result = subprocess.run([
            sys.executable, "-m", "content_task_agent", "--input", str(source),
            "--mode", "mock", "--output", str(output), "--runtime-dir", str(folder / "runtime"),
            "--trace-dir", str(folder / "traces"), "--now", "2030-01-15T09:00:00+08:00", *extra,
        ], cwd=ROOT, env=env, capture_output=True, timeout=30)
        return result, output

    def test_success_output_remains_utf8_despite_gbk_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.invoke({"task_id": "encoding", "topic": "校园环保", "budget": 10,
                                          "deadline": "2030-01-15T18:00:00+08:00"}, Path(directory))
            self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8"))
            parsed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(parsed, json.loads(result.stdout.decode("utf-8")))
            self.assertIn("校园环保", output.read_text(encoding="utf-8"))
            self.assertNotIn("\ufffd", output.read_text(encoding="utf-8"))
            self.assertEqual(parsed["plan"]["estimate"]["estimated_total_cents"], 900)

    def test_missing_input_also_produces_nonempty_utf8_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.invoke({"task_id": "missing", "topic": "校园环保"}, Path(directory))
            self.assertEqual(result.returncode, 2)
            parsed = json.loads(output.read_text(encoding="utf-8"))
            self.assertIn("缺少必填字段", parsed["reason"])
            self.assertEqual(parsed, json.loads(result.stderr.decode("utf-8")))

    def test_naive_now_is_rejected_before_running(self):
        with tempfile.TemporaryDirectory() as directory:
            result, output = self.invoke({"task_id": "now", "topic": "校园环保", "budget": 10,
                                          "deadline": "2030-01-15T18:00:00+08:00"}, Path(directory),
                                         "--now", "2030-01-15T09:00:00")
            self.assertEqual(result.returncode, 2)
            self.assertIn("时区", json.loads(output.read_text(encoding="utf-8"))["reason"])
