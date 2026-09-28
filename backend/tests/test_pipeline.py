import io
import tempfile
import time
import unittest
import zipfile
from pathlib import Path

from app import storage, task_manager
from app.services import archive_service
from app.services.archive_service import _default_output_name, _source_files, build_extract_worker
from app.services.tick_service import build_validate_worker, infer_product_from_file


class PipelineTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        storage.UPLOAD_DIR = root / "uploads"
        storage.ARTIFACT_DIR = root / "artifacts"
        storage.FILE_INDEX = root / "files.json"
        task_manager.TASK_DIR = root / "tasks"
        archive_service.EXTRACT_DIR = root / "extracted"
        for directory in (
            storage.UPLOAD_DIR,
            storage.ARTIFACT_DIR,
            task_manager.TASK_DIR,
            archive_service.EXTRACT_DIR,
        ):
            directory.mkdir(parents=True, exist_ok=True)
        self.file_store = storage.FileStore()
        self.tasks = task_manager.TaskManager()

    def tearDown(self):
        self.temporary.cleanup()

    @staticmethod
    def wait_for_task(manager, task_id):
        for _ in range(100):
            task = manager.snapshot(task_id, include_events=True)
            if task["status"] in {"success", "failed"}:
                return task
            time.sleep(0.03)
        raise AssertionError("task timed out")

    def test_extract_validate_and_event_history(self):
        log_text = "\n".join([
            "日志头",
            "STATS:已采集天数 1",
            'TICK_DATA:{code:"HC2610.XSGE", datetime:"2026-09-01 09:30:00", current:3500, high:3510, low:3490, volume:10, ask1_price:3501, bid1_price:3500}',
            'TICK_DATA:{code:"HC2610.XSGE", datetime:"2026-09-01 09:30:00", current:3501, high:3510, low:3490, volume:12, ask1_price:3502, bid1_price:3501}',
            "取消已订阅的所有标的",
            "日志尾",
        ])
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("sample/log.txt", log_text.encode("gbk"))
        archive_bytes.seek(0)
        upload = self.file_store.save_upload("sample.zip", archive_bytes)

        extract_task = self.tasks.create(
            "extract",
            {"file_ids": [upload["id"]], "output_name": "merged_log.txt"},
            build_extract_worker(self.file_store),
        )
        extract_result = self.wait_for_task(self.tasks, extract_task["id"])
        self.assertEqual(extract_result["status"], "success", extract_result.get("error"))
        self.assertEqual(extract_result["result"]["written_lines"], 2)
        self.assertTrue(any(event["type"] == "progress" for event in extract_result["events"]))

        artifact_id = extract_result["result"]["artifact"]["id"]
        validate_task = self.tasks.create(
            "validate",
            {"file_id": artifact_id, "adjust_duplicates": True},
            build_validate_worker(self.file_store),
        )
        validate_result = self.wait_for_task(self.tasks, validate_task["id"])
        self.assertEqual(validate_result["status"], "success", validate_result.get("error"))
        self.assertEqual(validate_result["result"]["valid_lines"], 2)
        self.assertEqual(validate_result["result"]["invalid_lines"], 0)
        self.assertEqual(validate_result["result"]["timestamp_adjustments"], 1)

    def test_clear_history_and_uploaded_archives(self):
        upload = self.file_store.save_upload("sample.zip", io.BytesIO(b"zip-content"))
        artifact_path = storage.ARTIFACT_DIR / "artifact.txt"
        artifact_path.write_text("artifact", encoding="utf-8")
        artifact = self.file_store.register_artifact("artifact.txt", artifact_path)
        task = self.tasks.create("noop", {}, lambda context, payload: {})
        self.wait_for_task(self.tasks, task["id"])

        task_result = self.tasks.clear_history()
        upload_result = self.file_store.clear_uploaded_archives()

        self.assertEqual(task_result["tasks"], 1)
        self.assertEqual(self.tasks.list(), [])
        self.assertEqual(list(task_manager.TASK_DIR.glob("*.json")), [])
        self.assertEqual(upload_result["upload_records"], 1)
        self.assertFalse((storage.UPLOAD_DIR / f"{upload['id']}_sample.zip").exists())
        self.assertEqual(self.file_store.get(artifact["id"])["kind"], "artifact")

    def test_extract_filters_mixed_products(self):
        log_text = "\n".join([
            "日志头",
            "STATS:已采集天数 1",
            'TICK_DATA:{tick_id:13527,total_ticks:31656,code:A2305.XDCE,underlying_symbol:A,commodity_name:豆一,exchange:XDCE,datetime:"2023-01-31 14:54:40.578",current:5521,open:5539,high:5540,low:5488,volume:76312,ask1_price:5522,bid1_price:5521}&#x20;',
            'TICK_DATA:{tick_id:22098,total_ticks:42956,code:C2303.XDCE,underlying_symbol:C,commodity_name:玉米,exchange:XDCE,datetime:"2023-01-31 15:09:06.129",current:2842,open:2888,high:2891,low:2837,volume:467314,ask1_price:2842,bid1_price:2841}',
            "取消已订阅的所有标的",
        ])
        archive_bytes = io.BytesIO()
        with zipfile.ZipFile(archive_bytes, "w") as archive:
            archive.writestr("A/log.txt", log_text.encode("gbk"))
        archive_bytes.seek(0)
        upload = self.file_store.save_upload("A_data.zip", archive_bytes)

        task = self.tasks.create(
            "extract",
            {
                "file_ids": [upload["id"]],
                "output_name": "A_clean.txt",
                "target_product": "A",
            },
            build_extract_worker(self.file_store),
        )
        result = self.wait_for_task(self.tasks, task["id"])
        self.assertEqual(result["status"], "success", result.get("error"))
        self.assertEqual(result["result"]["written_lines"], 1)
        self.assertEqual(result["result"]["rejected_lines"], 1)
        self.assertEqual(result["result"]["product_mismatch_lines"], 1)

        clean_file = self.file_store.get(result["result"]["artifact"]["id"])["resolved_path"]
        rejected_file = self.file_store.get(result["result"]["rejected_artifact"]["id"])["resolved_path"]
        clean_text = clean_file.read_text(encoding="utf-8")
        rejected_text = rejected_file.read_text(encoding="utf-8")
        self.assertIn("A2305.XDCE", clean_text)
        self.assertNotIn("C2303.XDCE", clean_text)
        self.assertNotIn("&#x20;", clean_text)
        self.assertIn("实际=C", rejected_text)
        self.assertIn("C2303.XDCE", rejected_text)
        self.assertEqual(infer_product_from_file(clean_file, "utf-8"), "A")

    def test_extract_reads_local_month_and_year_directories(self):
        root = Path(self.temporary.name) / "A" / "2023"
        month_one = root / "10月"
        month_two = root / "11月"
        month_one.mkdir(parents=True)
        month_two.mkdir(parents=True)

        def write_zip(directory, filename, tick_id):
            text = "\n".join([
                "STATS:已采集天数 1",
                f'TICK_DATA:{{tick_id:{tick_id},total_ticks:1,code:A2305.XDCE,underlying_symbol:A,commodity_name:豆一,exchange:XDCE,datetime:"2023-01-31 14:54:40.578",current:5521,open:5539,high:5540,low:5488,volume:1,ask1_price:5522,bid1_price:5521}}',
                "取消已订阅的所有标的",
            ])
            with zipfile.ZipFile(directory / filename, "w") as archive:
                archive.writestr("log.txt", text.encode("gbk"))

        write_zip(month_one, "A_10.zip", 1)
        write_zip(month_two, "A_11.zip", 2)

        month_files = self.file_store.list()
        self.assertEqual(month_files, [])

        year_task = self.tasks.create(
            "extract",
            {
                "file_ids": [],
                "source_directory": str(root),
                "merge_scope": "year",
                "target_product": "A",
                "output_name": "A_2023.txt",
            },
            build_extract_worker(self.file_store),
        )
        result = self.wait_for_task(self.tasks, year_task["id"])
        self.assertEqual(result["status"], "success", result.get("error"))
        self.assertEqual(result["result"]["zip_files"], 2)
        self.assertEqual(result["result"]["written_lines"], 2)

        month_task = self.tasks.create(
            "extract",
            {
                "file_ids": [],
                "source_directory": str(month_one),
                "merge_scope": "month",
                "target_product": "A",
                "output_name": "A_2023_10.txt",
            },
            build_extract_worker(self.file_store),
        )
        month_result = self.wait_for_task(self.tasks, month_task["id"])
        self.assertEqual(month_result["status"], "success", month_result.get("error"))
        self.assertEqual(month_result["result"]["zip_files"], 1)
        self.assertEqual(month_result["result"]["written_lines"], 1)

        month_as_year = self.tasks.create(
            "extract",
            {
                "file_ids": [],
                "source_directory": str(month_one),
                "merge_scope": "year",
                "target_product": "A",
                "output_name": "A_from_month.txt",
            },
            build_extract_worker(self.file_store),
        )
        month_as_year_result = self.wait_for_task(self.tasks, month_as_year["id"])
        self.assertEqual(month_as_year_result["status"], "success", month_as_year_result.get("error"))
        self.assertEqual(month_as_year_result["result"]["zip_files"], 1)

        with self.assertRaises(ValueError):
            _source_files(
                self.file_store,
                {"source_directory": str(root), "merge_scope": "month"},
            )

        selected_month_files, effective_directory, _ = _source_files(
            self.file_store,
            {
                "source_directory": str(root),
                "merge_scope": "month",
                "selected_month_directory": str(month_two),
            },
        )
        self.assertEqual(len(selected_month_files), 1)
        self.assertEqual(Path(effective_directory), month_two.resolve())

        selected_files, selected_effective, selected_note = _source_files(
            self.file_store,
            {
                "source_directory": str(root),
                "merge_scope": "month",
                "selected_month_directories": [str(month_two), str(month_one)],
            },
        )
        self.assertEqual(len(selected_files), 2)
        self.assertEqual(Path(selected_effective), root.resolve())
        self.assertIn("仅处理选中的月份", selected_note)

        self.assertEqual(
            _default_output_name(str(month_two), "month", "A"),
            "A_2023_11.txt",
        )
        self.assertEqual(
            _default_output_name(str(root), "year", "A"),
            "A_2023.txt",
        )
        self.assertEqual(
            _default_output_name(str(root), "month", "A", [str(month_one), str(month_two)]),
            "A_2023_10_11.txt",
        )


if __name__ == "__main__":
    unittest.main()
