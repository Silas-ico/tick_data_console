import io
import tempfile
import threading
import time
import unittest
import zipfile
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from app import storage, task_manager
from app.services import archive_service
from app.services.archive_service import _default_output_name, _source_files, build_extract_worker
from app.services.tick_service import build_import_worker, build_validate_worker, infer_product_from_file, query_tick_coverage


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
        self.assertEqual(extract_result["result"]["artifact"]["name"], "merged_log.txt")
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

    def test_year_extract_tasks_run_concurrently_without_mixing_outputs(self):
        barrier = threading.Barrier(2)
        workers_started = []
        workers_lock = threading.Lock()

        def make_worker():
            extract_worker = build_extract_worker(self.file_store)

            def worker(context, payload):
                with workers_lock:
                    workers_started.append(context.task_id)
                barrier.wait(timeout=3)
                return extract_worker(context, payload)

            return worker

        task_ids = []
        source_records = {}
        for year, tick_id in ((2021, 2101), (2022, 2201)):
            year_directory = Path(self.temporary.name) / "BU" / str(year)
            month_directory = year_directory / "1月"
            month_directory.mkdir(parents=True)
            log_text = "\n".join([
                "STATS:已采集天数 1",
                f'TICK_DATA:{{"tick_id": {tick_id}, "code": "BU2106.XSGE", "underlying_symbol": "BU", "datetime": "{year}-01-04 09:00:00", "current": 2500, "high": 2501, "low": 2499, "volume": 10, "ask1_price": 2501, "bid1_price": 2500}}',
                "取消已订阅的所有标的",
            ])
            with zipfile.ZipFile(month_directory / f"log-{year}.zip", "w") as archive:
                archive.writestr("log.txt", log_text.encode("gbk"))
            source_records[year] = tick_id
            task = self.tasks.create(
                "extract",
                {
                    "source_directory": str(year_directory),
                    "merge_scope": "year",
                    "target_product": "BU",
                    "output_name": f"BU_{year}.txt",
                    "display_name": str(year_directory),
                },
                make_worker(),
            )
            task_ids.append(task["id"])

        results = [self.wait_for_task(self.tasks, task_id) for task_id in task_ids]
        self.assertEqual(workers_started, task_ids)
        self.assertTrue(all(result["status"] == "success" for result in results))

        output_text = {}
        for year, result in zip((2021, 2022), results):
            artifact = self.file_store.get(result["result"]["artifact"]["id"])
            output_text[year] = artifact["resolved_path"].read_text(encoding="utf-8")
            self.assertIn(f'"tick_id": {source_records[year]}', output_text[year])
            other_year = 2022 if year == 2021 else 2021
            self.assertNotIn(f'"tick_id": {source_records[other_year]}', output_text[year])
        self.assertNotEqual(results[0]["result"]["artifact"]["id"], results[1]["result"]["artifact"]["id"])

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

    def test_artifact_preview_reads_utf8_in_bounded_pages(self):
        path = storage.ARTIFACT_DIR / "preview.txt"
        path.write_text("第一行\n第二行\n第三行\n", encoding="utf-8")
        artifact = self.file_store.register_artifact("preview.txt", path)

        first = self.file_store.read_artifact_preview(artifact["id"], limit=2)
        second = self.file_store.read_artifact_preview(artifact["id"], offset=first["next_offset"], limit=2)

        self.assertEqual(first["lines"], ["第一行", "第二行"])
        self.assertTrue(first["has_more"])
        self.assertEqual(second["lines"], ["第三行"])
        self.assertFalse(second["has_more"])
        for _ in range(100):
            if self.file_store.preview_index_status(artifact["id"])["status"] == "ready":
                break
            time.sleep(0.01)

    def test_artifact_preview_rejects_local_files(self):
        path = Path(self.temporary.name) / "source.txt"
        path.write_text("source", encoding="utf-8")
        local_file = self.file_store.register_local_path(path)

        with self.assertRaisesRegex(ValueError, "只能预览"):
            self.file_store.read_artifact_preview(local_file["id"])

    def test_artifact_preview_index_counts_and_jumps_to_line(self):
        path = storage.ARTIFACT_DIR / "indexed-preview.txt"
        path.write_text("\n".join(f"行 {number}" for number in range(1, 26)), encoding="utf-8")
        artifact = self.file_store.register_artifact("indexed-preview.txt", path)

        first = self.file_store.read_artifact_preview(artifact["id"], line=1, limit=3)
        self.assertEqual(first["lines"], ["行 1", "行 2", "行 3"])
        for _ in range(100):
            status = self.file_store.preview_index_status(artifact["id"])
            if status["status"] == "ready":
                break
            time.sleep(0.01)
        self.assertEqual(status["total_lines"], 25)

        jumped = self.file_store.read_artifact_preview(artifact["id"], line=21, limit=3)
        self.assertEqual(jumped["start_line"], 21)
        self.assertEqual(jumped["lines"], ["行 21", "行 22", "行 23"])
        self.assertTrue(jumped["has_more"])

    def test_progress_events_are_throttled_and_stored_incrementally(self):
        release_worker = threading.Event()
        task = self.tasks.create(
            "validate", {}, lambda _context, _payload: release_worker.wait(timeout=2) or {}
        )
        task_id = task["id"]
        self.tasks.update_progress(task_id, 1, "检查 500 行")
        self.tasks.update_progress(task_id, 1.01, "检查 1000 行")

        snapshot = self.tasks.snapshot(task_id, include_events=True)
        persisted = (task_manager.TASK_DIR / f"{task_id}.json").read_text(encoding="utf-8")
        event_lines = (task_manager.TASK_DIR / f"{task_id}.events.jsonl").read_text(encoding="utf-8").splitlines()
        try:
            self.assertEqual(snapshot["progress"], 1.01)
            self.assertEqual(sum(event["type"] == "progress" for event in snapshot["events"]), 1)
            self.assertNotIn('"events"', persisted)
            self.assertGreaterEqual(len(event_lines), 3)
        finally:
            release_worker.set()
            self.wait_for_task(self.tasks, task_id)

    def test_task_event_history_is_cursor_paginated(self):
        task = self.tasks.create("noop", {}, lambda _context, _payload: {})
        self.wait_for_task(self.tasks, task["id"])
        for index in range(7):
            self.tasks.emit(task["id"], "log", f"event {index}")

        latest = self.tasks.event_page(task["id"], limit=4)
        older = self.tasks.event_page(task["id"], before_id=latest["events"][0]["id"], limit=4)
        newer = self.tasks.event_page(task["id"], after_id=older["events"][-1]["id"], limit=4)

        self.assertEqual(len(latest["events"]), 4)
        self.assertTrue(latest["has_more"])
        self.assertEqual(older["events"][-1]["id"] + 1, latest["events"][0]["id"])
        self.assertEqual(newer["events"], latest["events"])

    def test_task_events_survive_manager_reload(self):
        task = self.tasks.create("noop", {}, lambda _context, _payload: {})
        finished = self.wait_for_task(self.tasks, task["id"])

        reloaded = task_manager.TaskManager()
        restored = reloaded.snapshot(task["id"], include_events=True)

        self.assertEqual(restored["status"], "success")
        self.assertEqual(restored["events"], finished["events"])

    def test_task_pause_resume_waits_at_checkpoint(self):
        first_archive_started = threading.Event()
        finish_first_archive = threading.Event()
        second_archive_started = threading.Event()

        def worker(context, payload):
            context.wait_if_paused()
            first_archive_started.set()
            finish_first_archive.wait(timeout=2)
            context.checkpoint(completed_archives=1)
            context.wait_if_paused()
            second_archive_started.set()
            return {"completed_archives": 2}

        task = self.tasks.create("extract", {}, worker)
        self.assertTrue(first_archive_started.wait(timeout=1))
        paused = self.tasks.pause(task["id"])
        self.assertEqual(paused["status"], "pausing")
        finish_first_archive.set()

        for _ in range(100):
            snapshot = self.tasks.snapshot(task["id"])
            if snapshot["status"] == "paused":
                break
            time.sleep(0.01)
        self.assertEqual(snapshot["status"], "paused")
        self.assertEqual(snapshot["checkpoint"]["completed_archives"], 1)
        self.assertFalse(second_archive_started.is_set())

        self.tasks.resume(task["id"])
        result = self.wait_for_task(self.tasks, task["id"])
        self.assertEqual(result["status"], "success")
        self.assertTrue(second_archive_started.is_set())

    def test_database_coverage_reports_real_year_month_and_day_gaps(self):
        class Cursor:
            def __init__(self):
                self.result = None
                self.rows = []
                self.sql = ""
                self.period_query_count = 0

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _params=None):
                self.sql = sql
                if "information_schema.statistics" in sql:
                    self.result = {"total": 1}
                elif "SELECT COUNT(*) AS total FROM `tick_data_A`" in sql:
                    if _params == (datetime(2024, 1, 1).date(), datetime(2025, 1, 1).date()):
                        self.result = {"total": 30}
                    else:
                        self.period_query_count += 1
                        self.result = {"total": [3, 8, 20][(self.period_query_count - 1) % 3]}
                elif "MIN(`tick_time`)" in sql:
                    self.result = {"min_time": datetime(2021, 1, 1), "max_time": datetime(2024, 3, 1)}
                elif "GROUP BY MONTH" in sql:
                    self.rows = [{"month": 1, "total": 10}, {"month": 3, "total": 20}]
                elif "GROUP BY DAY" in sql:
                    self.rows = [{"day": 2, "total": 10}, {"day": 4, "total": 10}]
                else:
                    self.rows = []

            def fetchone(self):
                return self.result

            def fetchall(self):
                return self.rows

        class Connection:
            def cursor(self):
                return Cursor()

            def close(self):
                pass

        config = {"database": "test"}
        with patch("app.services.tick_service.list_tick_tables", return_value=[
            {"name": "tick_data_A", "code": "A", "label": "A"},
        ]), patch("app.services.tick_service.connect_database", return_value=Connection()), \
                patch("app.services.tick_service._persist_coverage_cache"), \
                patch.dict("app.services.tick_service._coverage_year_cache", {}, clear=True):
            result = query_tick_coverage(config, table_name="tick_data_A", month=3)
            detail_result = query_tick_coverage(config, table_name="tick_data_A", year=2024, month=3)

        self.assertEqual([item["year"] for item in result["years"]], [2024, 2023, 2022, 2021])
        self.assertEqual([item["month"] for item in result["months"]], [1, 3])
        self.assertEqual([item["day"] for item in result["days"]], [2, 4])
        self.assertEqual(result["periods"], {"day": 3, "week": 8, "month": 20, "year": 0})
        self.assertIsNone(detail_result["years"])
        self.assertEqual(detail_result["selected_year_total"], 30)

    def test_import_result_and_log_record_file_months(self):
        source = Path(self.temporary.name) / "A_sample.txt"
        source.write_text("\n".join([
            'TICK_DATA:{code:"A2305.XDCE",underlying_symbol:"A",datetime:"2021-01-01 09:30:00",current:1}',
            'TICK_DATA:{code:"A2305.XDCE",underlying_symbol:"A",datetime:"2023-02-01 09:30:00",current:2}',
        ]), encoding="utf-8")
        file_item = self.file_store.register_local_path(source)

        class Cursor:
            rowcount = 1

            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def execute(self, sql, _params=None):
                self.sql = sql

            def fetchone(self):
                return {"count": 2, "min_time": "2021-01-01", "max_time": "2023-02-01"}

        class Connection:
            def cursor(self):
                return Cursor()

            def commit(self):
                pass

            def rollback(self):
                pass

            def close(self):
                pass

        class Context:
            def __init__(self):
                self.messages = []

            def log(self, message):
                self.messages.append(message)

            def progress(self, *_args):
                pass

        context = Context()
        with patch("app.services.tick_service.ensure_database"), \
                patch("app.services.tick_service.connect_database", return_value=Connection()), \
                patch("app.services.tick_service.table_exists", return_value=True):
            result = build_import_worker(self.file_store, lambda: {
                "host": "localhost", "port": 3306, "database": "test",
            })(context, {"file_id": file_item["id"], "table_name": "tick_data_A"})

        self.assertEqual(result["file_months"], ["2021-01", "2023-02"])
        self.assertEqual(result["file_month_range"], {"start": "2021-01", "end": "2023-02"})
        self.assertTrue(any("2021年1月、2023年2月" in message for message in context.messages))

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

    def test_archive_month_directory_identifies_year_subfolder(self):
        root = Path(self.temporary.name) / "BU" / "2022"
        archive_path = root / "10月" / "nested" / "log-0105.zip"
        self.assertEqual(archive_service.archive_month_directory(archive_path, root), "10月")
        self.assertEqual(archive_service.archive_month_directory(archive_path, root / "10月"), "10月")
        self.assertIsNone(archive_service.archive_month_directory(root / "log-0105.zip", root))

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

        ordered_paths = sorted(
            [
                root / "10月" / "nested" / "A_10.zip",
                root / "2月" / "A_02.zip",
                root / "1月" / "A_01.zip",
            ],
            key=archive_service._archive_sort_key,
        )
        self.assertEqual([archive_service._archive_period_label(path) for path in ordered_paths], [
            "2023年1月", "2023年2月", "2023年10月",
        ])

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
        extract_messages = [event["message"] for event in result["events"] if event["type"] == "log"]
        self.assertTrue(any("2023年10月" in message for message in extract_messages))
        self.assertTrue(any("2023年11月" in message for message in extract_messages))

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
