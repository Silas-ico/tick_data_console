import json
import re
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import BinaryIO

from .config import ARTIFACT_DIR, EXTRACT_DIR, FILE_INDEX, UPLOAD_DIR, ensure_data_dirs


SAFE_NAME_RE = re.compile(r"[^\w.\-()\u4e00-\u9fff]+", re.UNICODE)


class FileStore:
    def __init__(self) -> None:
        self._lock = RLock()
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
