import tempfile
import unittest
from pathlib import Path

from src.content_task_agent.storage import JsonStore, StorageError


class StorageTests(unittest.TestCase):
    def test_versions_and_atomic_plan_save(self):
        with tempfile.TemporaryDirectory() as directory:
            store = JsonStore(Path(directory))
            with store.locked():
                version, cached = store.open_version("task", "a")
                self.assertEqual((version, cached), (1, None))
                result = {"status": "completed"}
                store.update("task", version, result)
                same_version, same_result = store.open_version("task", "a")
                self.assertEqual(same_version, 1)
                self.assertEqual(same_result, result)
                new_version, new_result = store.open_version("task", "b")
                self.assertEqual((new_version, new_result), (2, None))
                saved = store.save("task", version, {"status": "completed"})
                self.assertTrue(Path(saved).exists())
                with self.assertRaises(StorageError):
                    store.save("task", version, {"status": "completed"})
