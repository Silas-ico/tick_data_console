import json
from pathlib import Path
from threading import RLock


BACKEND_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ARTIFACT_DIR = DATA_DIR / "artifacts"
EXTRACT_DIR = DATA_DIR / "extracted"
TASK_DIR = DATA_DIR / "tasks"
FILE_INDEX = DATA_DIR / "files.json"
DATABASE_CONFIG_FILE = DATA_DIR / "database.json"

DEFAULT_DATABASE_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "",
    "database": "jq_category",
    "charset": "utf8mb4",
}

_config_lock = RLock()


def ensure_data_dirs() -> None:
    for directory in (DATA_DIR, UPLOAD_DIR, ARTIFACT_DIR, EXTRACT_DIR, TASK_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_database_config() -> dict:
    ensure_data_dirs()
    with _config_lock:
        if not DATABASE_CONFIG_FILE.exists():
            save_database_config(DEFAULT_DATABASE_CONFIG)
        try:
            saved = json.loads(DATABASE_CONFIG_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            saved = {}
        return {**DEFAULT_DATABASE_CONFIG, **saved}


def save_database_config(config: dict) -> dict:
    ensure_data_dirs()
    normalized = {**DEFAULT_DATABASE_CONFIG, **config}
    normalized["port"] = int(normalized["port"])
    with _config_lock:
        temporary = DATABASE_CONFIG_FILE.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(normalized, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(DATABASE_CONFIG_FILE)
    return normalized
