import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock, Thread
from typing import BinaryIO

from .config import ARTIFACT_DIR, EXTRACT_DIR, FILE_INDEX, UPLOAD_DIR, ensure_data_dirs


SAFE_NAME_RE = re.compile(r"[^\w.\-()\u4e00-\u9fff]+", re.UNICODE)
PREVIEW_INDEX_STRIDE = 10_000
PREVIEW_READ_SIZE = 4 * 1024 * 1024


class FileStore:
    def __init__(self) -> None:
        self._lock = RLock()
        self._preview_lock = RLock()
        self._preview_jobs = {}
        ensure_data_dirs()
        self._files = self._load()

    def _load(self) -> dict:
        if not FILE_INDEX.exists():
            return {}
        try:
            items = json.loads(FILE_INDEX.read_text(encoding="utf-8"))
            return {item["id"]: item for item in items}
        except (OSError, json.JSONDecodeError, KeyError, TypeError):
            return {}

    def _persist(self) -> None:
        temporary = FILE_INDEX.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(list(self._files.values()), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(FILE_INDEX)

    @staticmethod
    def _safe_name(filename: str) -> str:
        plain_name = Path(filename).name.strip() or "unnamed_file"
        return SAFE_NAME_RE.sub("_", plain_name)

    def save_upload(self, filename: str, source: BinaryIO) -> dict:
        file_id = uuid.uuid4().hex
        safe_name = self._safe_name(filename)
        destination = UPLOAD_DIR / f"{file_id}_{safe_name}"
        size = 0
        with destination.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
                size += len(chunk)
        return self._register(file_id, safe_name, destination, size, "upload")

    def register_artifact(self, filename: str, path: Path) -> dict:
        file_id = uuid.uuid4().hex
        safe_name = self._safe_name(filename)
        final_path = ARTIFACT_DIR / f"{file_id}_{safe_name}"
        if path.resolve() != final_path.resolve():
            path.replace(final_path)
        return self._register(file_id, safe_name, final_path, final_path.stat().st_size, "artifact")

    def register_local_path(self, path: Path) -> dict:
        resolved = path.resolve()
        with self._lock:
            for item in self._files.values():
                if Path(item.get("path", "")).resolve() == resolved:
                    return self.public_item(item)
        return self._register(
            uuid.uuid5(uuid.NAMESPACE_URL, str(resolved)).hex,
            resolved.name,
            resolved,
            resolved.stat().st_size,
            "local",
        )

    def _register(self, file_id: str, name: str, path: Path, size: int, kind: str) -> dict:
        item = {
            "id": file_id,
            "name": name,
            "size": size,
            "kind": kind,
            "extension": path.suffix.lower(),
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "path": str(path.resolve()),
        }
        with self._lock:
            self._files[file_id] = item
            self._persist()
        return self.public_item(item)

    @staticmethod
    def public_item(item: dict) -> dict:
        return {key: value for key, value in item.items() if key not in {"path", "resolved_path"}}

    def get(self, file_id: str) -> dict:
        with self._lock:
            item = self._files.get(file_id)
            if not item:
                raise KeyError(file_id)
            path = Path(item["path"])
            if not path.exists():
                raise FileNotFoundError(path)
            return {**item, "resolved_path": path}

    def _preview_artifact(self, file_id: str) -> dict:
        item = self.get(file_id)
        path = item["resolved_path"]
        artifact_root = ARTIFACT_DIR.resolve()
        if item.get("kind") != "artifact" or artifact_root not in path.parents:
            raise ValueError("只能预览项目生成的合并日志文件")
        if item.get("extension") not in {".txt", ".log"}:
            raise ValueError("仅支持预览 TXT 或 LOG 文件")
        return item

    @staticmethod
    def _index_path(item: dict) -> Path:
        index_directory = FILE_INDEX.parent / "preview_indexes"
        index_directory.mkdir(parents=True, exist_ok=True)
        return index_directory / f"{item['id']}.json"

    def _load_preview_index(self, item: dict) -> dict | None:
        index_path = self._index_path(item)
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        stat = item["resolved_path"].stat()
        if index.get("size") != stat.st_size or index.get("mtime_ns") != stat.st_mtime_ns:
            return None
        return index

    def preview_index_status(self, file_id: str) -> dict:
        item = self._preview_artifact(file_id)
        index = self._load_preview_index(item)
        if index:
            return {"status": "ready", "progress": 100, "total_lines": index["total_lines"]}
        with self._preview_lock:
            job = self._preview_jobs.get(file_id)
            if job and job["status"] == "indexing":
                return {"status": "indexing", "progress": job["progress"], "total_lines": None}
            if job and job["status"] == "failed":
                return {"status": "failed", "progress": job["progress"], "total_lines": None, "error": job["error"]}
            job = {"status": "indexing", "progress": 0, "total_lines": None, "error": None}
            self._preview_jobs[file_id] = job
        Thread(target=self._build_preview_index, args=(file_id, item, job), daemon=True).start()
        return {"status": "indexing", "progress": 0, "total_lines": None}

    def _build_preview_index(self, file_id: str, item: dict, job: dict) -> None:
        path = item["resolved_path"]
        stat = path.stat()
        offsets = [0]
        newline_count = 0
        next_checkpoint = PREVIEW_INDEX_STRIDE
        last_byte = b""
        try:
            with path.open("rb") as source:
                while chunk := source.read(PREVIEW_READ_SIZE):
                    chunk_start = source.tell() - len(chunk)
                    newline_total = chunk.count(b"\n")
                    while newline_count + newline_total >= next_checkpoint:
                        remaining = next_checkpoint - newline_count
                        position = -1
                        for _ in range(remaining):
                            position = chunk.find(b"\n", position + 1)
                        offsets.append(chunk_start + position + 1)
                        next_checkpoint += PREVIEW_INDEX_STRIDE
                    newline_count += newline_total
                    last_byte = chunk[-1:]
                    with self._preview_lock:
                        job["progress"] = min(99, int(source.tell() * 100 / max(stat.st_size, 1)))
            total_lines = newline_count + (1 if stat.st_size and last_byte != b"\n" else 0)
            index = {
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "total_lines": total_lines,
                "stride": PREVIEW_INDEX_STRIDE,
                "offsets": offsets,
            }
            index_path = self._index_path(item)
            temporary = index_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(index), encoding="utf-8")
            temporary.replace(index_path)
            with self._preview_lock:
                job.update(status="ready", progress=100, total_lines=total_lines)
        except Exception as exc:
            with self._preview_lock:
                job.update(status="failed", error=str(exc))

    def read_artifact_preview(self, file_id: str, offset: int = 0, limit: int = 100, line: int | None = None) -> dict:
        item = self._preview_artifact(file_id)
        path = item["resolved_path"]
        if offset < 0 or offset > item["size"]:
            raise ValueError("预览位置超出文件范围")

        index = self._load_preview_index(item)
        index_status = self.preview_index_status(file_id) if not index else {
            "status": "ready", "progress": 100, "total_lines": index["total_lines"]
        }
        start_line = None
        if line is not None:
            if line < 1:
                raise ValueError("行号必须大于或等于 1")
            if not index:
                if index_status["status"] == "failed":
                    raise ValueError("行号索引建立失败")
                if line > 1:
                    return {"lines": [], "indexing": True, **index_status, "size": item["size"]}
                start_line = 1
            elif line > index["total_lines"]:
                raise ValueError(f"行号超出范围，文件共 {index['total_lines']} 行")
            else:
                start_line = line
                checkpoint = (line - 1) // index["stride"]
                offset = index["offsets"][checkpoint]
                line = (line - 1) % index["stride"] + 1

        lines = []
        with path.open("rb") as source:
            if offset:
                source.seek(offset - 1)
                if source.read(1) != b"\n":
                    source.readline()
            else:
                source.seek(0)
            start_offset = source.tell()
            if line is not None:
                for _ in range(line - 1):
                    if not source.readline():
                        break
                start_offset = source.tell()
            for _ in range(limit):
                raw_line = source.readline(16 * 1024 * 1024)
                if not raw_line:
                    break
                lines.append(raw_line.decode("utf-8", errors="replace").rstrip("\r\n"))
            next_offset = source.tell()
            has_more = source.read(1) != b""
        if start_line is None and index:
            start_line = (index["offsets"].index(start_offset) * index["stride"] + 1) if start_offset in index["offsets"] else None
        return {
            "lines": lines,
            "offset": start_offset,
            "next_offset": next_offset,
            "has_more": has_more,
            "size": item["size"],
            "indexing": not bool(index),
            "total_lines": index["total_lines"] if index else None,
            "start_line": start_line,
            "index_progress": index_status["progress"],
        }

    def list(self) -> list[dict]:
        with self._lock:
            existing = [
                self.public_item(item)
                for item in self._files.values()
                if item.get("kind") != "local" and Path(item["path"]).exists()
            ]
        return sorted(existing, key=lambda item: item["created_at"], reverse=True)

    def stats(self) -> dict:
        with self._lock:
            items = list(self._files.values())
        managed = [item for item in items if item.get("kind") != "local"]
        available = [item for item in managed if Path(item["path"]).exists()]
        return {
            "indexed": len(managed),
            "available": len(available),
            "missing": len(managed) - len(available),
            "zip": sum(item.get("extension", "").lower() == ".zip" for item in available),
            "logs": sum(item.get("extension", "").lower() in {".txt", ".log"} for item in available),
        }

    def clear_managed_files(self) -> dict:
        with self._lock:
            removable_ids = [
                file_id
                for file_id, item in self._files.items()
                if item.get("kind") in {"upload", "artifact", "local"}
            ]
            preserved_local_files = sum(
                item.get("kind") == "local"
                and Path(item.get("path", "")).exists()
                for item in self._files.values()
            )
            for file_id in removable_ids:
                self._files.pop(file_id, None)
            if removable_ids:
                self._persist()

        removed_upload_files = 0
        upload_root = UPLOAD_DIR.resolve()
        for child in UPLOAD_DIR.iterdir():
            if child.is_file() and child.resolve().parent == upload_root:
                child.unlink()
                removed_upload_files += 1

        removed_artifact_files = 0
        artifact_root = ARTIFACT_DIR.resolve()
        for child in ARTIFACT_DIR.iterdir():
            if child.is_file() and child.resolve().parent == artifact_root:
                child.unlink()
                removed_artifact_files += 1

        removed_extract_directories = 0
        extract_root = EXTRACT_DIR.resolve()
        for child in EXTRACT_DIR.iterdir():
            if child.resolve().parent != extract_root:
                continue
            if child.is_dir():
                shutil.rmtree(child)
                removed_extract_directories += 1
            elif child.is_file():
                child.unlink()

        return {
            "removed_index_records": len(removable_ids),
            "removed_upload_files": removed_upload_files,
            "removed_artifact_files": removed_artifact_files,
            "removed_extract_directories": removed_extract_directories,
            "preserved_local_files": preserved_local_files,
        }

    def clear_uploaded_archives(self) -> dict:
        with self._lock:
            upload_ids = [
                file_id
                for file_id, item in self._files.items()
                if item.get("kind") == "upload" and item.get("extension", "").lower() == ".zip"
            ]
            for file_id in upload_ids:
                self._files.pop(file_id, None)

            removed_files = 0
            upload_root = UPLOAD_DIR.resolve()
            for upload_path in UPLOAD_DIR.iterdir():
                if (
                    upload_path.is_file()
                    and upload_path.suffix.lower() == ".zip"
                    and upload_path.resolve().parent == upload_root
                ):
                    upload_path.unlink()
                    removed_files += 1

            if upload_ids:
                self._persist()

        return {"upload_records": len(upload_ids), "upload_files": removed_files}
