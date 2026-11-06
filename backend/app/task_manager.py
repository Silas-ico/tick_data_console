import asyncio
from bisect import bisect_left, bisect_right
import json
import os
import tempfile
import time
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Callable, List

from .config import TASK_DIR, ensure_data_dirs


TERMINAL_STATUSES = {"success", "failed"}
PAUSABLE_STATUSES = {"pending", "running"}


class TaskContext:
    def __init__(self, manager: "TaskManager", task_id: str) -> None:
        self.manager = manager
        self.task_id = task_id

    def log(self, message: str, level: str = "info", data: dict | None = None) -> None:
        self.manager.emit(self.task_id, "log", message, level=level, data=data)

    def progress(self, value: float, message: str = "") -> None:
        self.manager.update_progress(self.task_id, value, message)

    def wait_if_paused(self) -> None:
        self.manager.wait_if_paused(self.task_id)

    def checkpoint(self, **values) -> None:
        self.manager.checkpoint(self.task_id, values)


class TaskManager:
    def __init__(self) -> None:
        self._lock = RLock()
        ensure_data_dirs()
        self._tasks: dict[str, dict] = {}
        self._pause_conditions: dict[str, threading.Condition] = {}
        self._last_progress_event: dict[str, tuple[float, float]] = {}
        self._load_tasks()

    def _load_tasks(self) -> None:
        for task_file in TASK_DIR.glob("*.json"):
            try:
                task = json.loads(task_file.read_text(encoding="utf-8"))
                event_path = self._event_path(task["id"])
                if event_path.exists():
                    task["events"] = []
                    for line in event_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        try:
                            task["events"].append(json.loads(line))
                        except json.JSONDecodeError:
                            continue
                else:
                    task.setdefault("events", [])
                    if task["events"]:
                        event_path.write_text(
                            "".join(json.dumps(event, ensure_ascii=False, default=str) + "\n" for event in task["events"]),
                            encoding="utf-8",
                        )
                task["next_event_id"] = max(
                    task.get("next_event_id", 1),
                    task["events"][-1]["id"] + 1 if task["events"] else 1,
                )
                if task.get("status") in {"pending", "running", "pausing", "paused"}:
                    task["status"] = "failed"
                    task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                    task["error"] = "服务重启，任务已中断；进程内暂停任务不能跨重启续传"
                    self._append_event(task, "status", task["error"], level="error")
                    self._persist(task)
                self._tasks[task["id"]] = task
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue

    def _task_path(self, task_id: str) -> Path:
        return TASK_DIR / f"{task_id}.json"

    def _event_path(self, task_id: str) -> Path:
        return TASK_DIR / f"{task_id}.events.jsonl"

    def _persist(self, task: dict) -> None:
        """Persist a task safely on Windows, including transient file locks."""
        target = self._task_path(task["id"])
        payload = json.dumps(
            {key: value for key, value in task.items() if key != "events"},
            ensure_ascii=False,
            indent=2,
            default=str,
        )
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{task['id']}.", suffix=".tmp", dir=str(TASK_DIR)
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            last_error = None
            for attempt in range(5):
                try:
                    os.replace(temporary, target)
                    return
                except PermissionError as exc:
                    last_error = exc
                    if attempt == 4:
                        raise
                    time.sleep(0.05 * (attempt + 1))
            if last_error:
                raise last_error
        finally:
            temporary.unlink(missing_ok=True)

    def _append_event(
        self,
        task: dict,
        event_type: str,
        message: str,
        level: str = "info",
        data: dict | None = None,
    ) -> dict:
        event = {
            "id": task.get("next_event_id", 1),
            "type": event_type,
            "level": level,
            "message": message,
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "progress": task.get("progress", 0),
            "status": task.get("status", "pending"),
            "data": data or {},
        }
        task.setdefault("events", []).append(event)
        task["next_event_id"] = event["id"] + 1
        with self._event_path(task["id"]).open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
        return event

    def create(self, task_type: str, payload: dict, worker: Callable[[TaskContext, dict], dict]) -> dict:
        task_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        task = {
            "id": task_id,
            "type": task_type,
            "status": "pending",
            "progress": 0,
            "message": "任务已创建，等待开始",
            "payload": payload,
            "result": None,
            "error": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "events": [],
            "next_event_id": 1,
            "checkpoint": {},
        }
        self._append_event(task, "status", "任务已创建，等待开始")
        with self._lock:
            self._tasks[task_id] = task
            self._pause_conditions[task_id] = threading.Condition(self._lock)
            self._persist(task)

        thread = threading.Thread(
            target=self._run,
            args=(task_id, worker),
            name=f"task-{task_id[:8]}",
            daemon=True,
        )
        thread.start()
        return self.snapshot(task_id)

    def _run(self, task_id: str, worker: Callable[[TaskContext, dict], dict]) -> None:
        with self._lock:
            task = self._tasks[task_id]
            if task["status"] not in {"paused", "pausing"}:
                task["status"] = "running"
                task["started_at"] = datetime.now().isoformat(timespec="seconds")
                task["message"] = "任务正在运行"
                self._append_event(task, "status", "任务开始运行")
            self._persist(task)

        context = TaskContext(self, task_id)
        try:
            result = worker(context, self._tasks[task_id]["payload"]) or {}
            with self._lock:
                task = self._tasks[task_id]
                task["status"] = "success"
                task["progress"] = 100
                task["message"] = "任务已完成"
                task["result"] = result
                task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._append_event(task, "result", "任务已完成", data=result)
                self._persist(task)
        except Exception as exc:
            with self._lock:
                task = self._tasks[task_id]
                task["status"] = "failed"
                task["message"] = "任务失败，请查看错误详情"
                task["error"] = str(exc)
                task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._append_event(
                    task,
                    "error",
                    f"任务失败：{exc}",
                    level="error",
                    data={"traceback": traceback.format_exc(limit=8)},
                )
                self._persist(task)

    def emit(
        self,
        task_id: str,
        event_type: str,
        message: str,
        level: str = "info",
        data: dict | None = None,
    ) -> dict:
        with self._lock:
            task = self._tasks[task_id]
            task["message"] = message
            event = self._append_event(task, event_type, message, level, data)
            self._persist(task)
            return event

    def update_progress(self, task_id: str, value: float, message: str = "") -> None:
        with self._lock:
            task = self._tasks[task_id]
            previous_progress = task.get("progress", 0)
            task["progress"] = max(0, min(100, round(float(value), 2)))
            if message:
                task["message"] = message
            now = time.monotonic()
            last_time, last_progress = self._last_progress_event.get(
                task_id, (0.0, previous_progress)
            )
            if now - last_time < 1.0 and task["progress"] - last_progress < 0.5:
                return
            self._append_event(task, "progress", message or "进度更新")
            self._last_progress_event[task_id] = (now, task["progress"])
            self._persist(task)

    def checkpoint(self, task_id: str, values: dict) -> None:
        with self._lock:
            task = self._tasks[task_id]
            task.setdefault("checkpoint", {}).update(values)
            self._persist(task)

    def wait_if_paused(self, task_id: str) -> None:
        with self._lock:
            condition = self._pause_conditions[task_id]
            task = self._tasks[task_id]
            if task.get("status") == "pausing":
                task["status"] = "paused"
                task["message"] = "任务已暂停，等待继续"
                self._append_event(task, "status", "任务已暂停，可继续运行")
                self._persist(task)
            while self._tasks[task_id].get("status") == "paused":
                condition.wait()
            task = self._tasks[task_id]
            if task.get("status") == "pending":
                task["status"] = "running"
                task["started_at"] = task.get("started_at") or datetime.now().isoformat(timespec="seconds")
                task["message"] = "任务正在运行"
                self._append_event(task, "status", "任务继续运行")
                self._persist(task)

    def pause(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.get("status") not in PAUSABLE_STATUSES:
                raise RuntimeError("只有等待中或运行中的任务可以暂停")
            if task.get("type") != "extract":
                raise RuntimeError("当前只有压缩处理任务支持暂停")
            task["status"] = "pausing"
            task["message"] = "正在完成当前压缩包，随后暂停"
            self._append_event(task, "status", "已收到暂停请求，当前压缩包处理完后暂停")
            self._persist(task)
            return self.snapshot(task_id)

    def resume(self, task_id: str) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            if task.get("status") not in {"paused", "pausing"}:
                raise RuntimeError("只有已暂停的任务可以继续")
            task["status"] = "running"
            task["message"] = "任务正在继续"
            self._append_event(task, "status", "任务继续运行")
            self._persist(task)
            condition = self._pause_conditions.get(task_id)
            if condition:
                condition.notify_all()
            return self.snapshot(task_id)

    def snapshot(self, task_id: str, include_events: bool = False) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            result = dict(task)
            result["last_event_id"] = task.get("next_event_id", 1) - 1
            if not include_events:
                result.pop("events", None)
                result.pop("next_event_id", None)
            return result

    def list(self) -> list[dict]:
        with self._lock:
            tasks = [self.snapshot(task_id) for task_id in self._tasks]
        return sorted(tasks, key=lambda task: task["created_at"], reverse=True)

    def clear_history(self) -> dict:
        with self._lock:
            active_tasks = [
                task_id
                for task_id, task in self._tasks.items()
                if task.get("status") not in TERMINAL_STATUSES
            ]
            if active_tasks:
                raise RuntimeError(f"仍有 {len(active_tasks)} 个任务正在运行，不能清空历史记录")

            task_count = len(self._tasks)
            task_files = list(TASK_DIR.glob("*.json"))
            for task_file in task_files:
                task_file.unlink(missing_ok=True)
            for event_file in TASK_DIR.glob("*.events.jsonl"):
                event_file.unlink(missing_ok=True)
            self._tasks.clear()
            self._last_progress_event.clear()

        return {"tasks": task_count, "task_files": len(task_files)}

    async def event_stream(self, task_id: str, last_event_id: int = 0):
        cursor = last_event_id
        idle_rounds = 0
        while True:
            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                events = self._events_after(task, cursor, 250)
                status = task["status"]

            if events:
                idle_rounds = 0
                for event in events:
                    cursor = event["id"]
                    payload = json.dumps(event, ensure_ascii=False, default=str)
                    yield f"id: {event['id']}\nevent: {event['type']}\ndata: {payload}\n\n"
            else:
                idle_rounds += 1
                if status in TERMINAL_STATUSES:
                    return
                if idle_rounds >= 20:
                    idle_rounds = 0
                    yield ": keep-alive\n\n"
            await asyncio.sleep(0.5)

    @staticmethod
    def _events_after(task: dict, cursor: int, limit: int) -> List[dict]:
        events = task.get("events", [])
        if not events:
            return []
        start = bisect_right(events, cursor, key=lambda event: event["id"])
        return events[start:start + limit]

    def event_page(
        self,
        task_id: str,
        *,
        before_id: int | None = None,
        after_id: int | None = None,
        limit: int = 500,
    ) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            events = task.get("events", [])
            if before_id is not None:
                end = bisect_left(events, before_id, key=lambda event: event["id"])
                page = events[max(0, end - limit):end]
                has_more = bool(page) and page[0]["id"] > events[0]["id"]
            elif after_id is not None:
                start = bisect_right(events, after_id, key=lambda event: event["id"])
                page = events[start:start + limit]
                has_more = bool(page) and page[-1]["id"] < events[-1]["id"]
            else:
                page = events[-limit:]
                has_more = len(events) > len(page)
            return {"events": list(page), "has_more": has_more}
