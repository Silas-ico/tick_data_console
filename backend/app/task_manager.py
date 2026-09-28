import asyncio
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
from typing import Callable

from .config import TASK_DIR, ensure_data_dirs


TERMINAL_STATUSES = {"success", "failed"}


class TaskContext:
    def __init__(self, manager: "TaskManager", task_id: str) -> None:
        self.manager = manager
        self.task_id = task_id

    def log(self, message: str, level: str = "info", data: dict | None = None) -> None:
        self.manager.emit(self.task_id, "log", message, level=level, data=data)

    def progress(self, value: float, message: str = "") -> None:
        self.manager.update_progress(self.task_id, value, message)


class TaskManager:
    def __init__(self) -> None:
        self._lock = RLock()
        ensure_data_dirs()
        self._tasks: dict[str, dict] = {}
        self._load_tasks()

    def _load_tasks(self) -> None:
        for task_file in TASK_DIR.glob("*.json"):
            try:
                task = json.loads(task_file.read_text(encoding="utf-8"))
                if task.get("status") in {"pending", "running"}:
                    task["status"] = "failed"
                    task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                    task["error"] = "服务重启，原后台任务已中断"
                    self._append_event(task, "status", "服务重启，任务已标记为失败", level="error")
                    self._persist(task)
                self._tasks[task["id"]] = task
            except (OSError, json.JSONDecodeError, KeyError, TypeError):
                continue

    def _task_path(self, task_id: str) -> Path:
        return TASK_DIR / f"{task_id}.json"

    def _persist(self, task: dict) -> None:
        """Persist a task safely on Windows, including transient file locks."""
        target = self._task_path(task["id"])
        payload = json.dumps(task, ensure_ascii=False, indent=2)
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

    @staticmethod
    def _append_event(
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
        return event

    def create(self, task_type: str, payload: dict, worker: Callable[[TaskContext, dict], dict]) -> dict:
        task_id = uuid.uuid4().hex
        now = datetime.now().isoformat(timespec="seconds")
        task = {
            "id": task_id,
            "type": task_type,
            "status": "pending",
            "progress": 0,
            "message": "任务等待启动",
            "payload": payload,
            "result": None,
            "error": None,
            "created_at": now,
            "started_at": None,
            "finished_at": None,
            "events": [],
            "next_event_id": 1,
        }
        self._append_event(task, "status", "任务已创建")
        with self._lock:
            self._tasks[task_id] = task
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
                task["message"] = "任务执行完成"
                task["result"] = result
                task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._append_event(task, "result", "任务执行完成", data=result)
                self._persist(task)
        except Exception as exc:
            with self._lock:
                task = self._tasks[task_id]
                task["status"] = "failed"
                task["message"] = "任务执行失败"
                task["error"] = str(exc)
                task["finished_at"] = datetime.now().isoformat(timespec="seconds")
                self._append_event(
                    task,
                    "error",
                    str(exc),
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
            task["progress"] = max(0, min(100, round(float(value), 2)))
            if message:
                task["message"] = message
            self._append_event(task, "progress", message or "进度更新")
            self._persist(task)

    def snapshot(self, task_id: str, include_events: bool = False) -> dict:
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise KeyError(task_id)
            result = dict(task)
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
            self._tasks.clear()

        return {"tasks": task_count, "task_files": len(task_files)}

    async def event_stream(self, task_id: str, last_event_id: int = 0):
        cursor = last_event_id
        idle_rounds = 0
        while True:
            with self._lock:
                task = self._tasks.get(task_id)
                if not task:
                    return
                events = [event for event in task["events"] if event["id"] > cursor]
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
