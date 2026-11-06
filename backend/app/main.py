import os
from typing import Annotated
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .config import load_database_config, save_database_config
from .services.archive_service import MONTH_DIRECTORY_RE, _archive_sort_key, archive_month_directory, build_extract_worker
from .services.tick_service import build_import_worker, build_validate_worker, list_tick_tables, query_tick_coverage, query_tick_records, test_database
from .storage import FileStore
from .task_manager import TaskManager


app = FastAPI(title="Tick Data Console API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:5174", "http://127.0.0.1:5174",
        "http://localhost:5175", "http://127.0.0.1:5175",
        "http://192.168.0.109:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

file_store = FileStore()
task_manager = TaskManager()


class DatabaseConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 3306
    user: str = "root"
    password: str = ""
    database: str = "jq_category"
    charset: str = "utf8mb4"


class ExtractRequest(BaseModel):
    file_ids: list[str] = Field(default_factory=list)
    output_name: str | None = None
    target_product: str | None = None
    source_directory: str | None = None
    merge_scope: str = "month"
    selected_month_directory: str | None = None
    selected_month_directories: list[str] = Field(default_factory=list)


class LocalArchiveRequest(BaseModel):
    directory: str
    scope: str = "month"
    selected_month_directory: str | None = None
    selected_month_directories: list[str] = Field(default_factory=list)


class LocalDirectoryPickerRequest(BaseModel):
    initial_directory: str = ""


class FileTaskRequest(BaseModel):
    file_id: str
    adjust_duplicates: bool = True


class ImportRequest(FileTaskRequest):
    table_name: str


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/config/database")
def get_database_config():
    return load_database_config()


@app.put("/api/config/database")
def update_database_config(config: DatabaseConfig):
    return save_database_config(config.model_dump())


@app.post("/api/config/database/test")
def test_database_config(config: DatabaseConfig):
    try:
        result = test_database(config.model_dump())
        return {"success": True, **result}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/database/tables")
def get_database_tables():
    try:
        return {"tables": list_tick_tables(load_database_config())}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取本地数据库表失败: {exc}") from exc


@app.get("/api/database/records")
def get_database_records(
    table_name: str = "",
    keyword: str = "",
    code: str = "",
    start_time: str = "",
    end_time: str = "",
    page: int = 1,
    page_size: int = 50,
):
    try:
        return query_tick_records(
            load_database_config(),
            table_name=table_name,
            keyword=keyword,
            code=code,
            start_time=start_time,
            end_time=end_time,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"查询数据库数据失败: {exc}") from exc


@app.get("/api/database/coverage")
def get_database_coverage(table_name: str, year: int | None = None, month: int | None = None):
    try:
        return query_tick_coverage(load_database_config(), table_name, year=year, month=month)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"读取数据覆盖统计失败: {exc}") from exc


@app.post("/api/files/upload")
def upload_files():
    raise HTTPException(status_code=410, detail="当前仅支持从本机目录读取 ZIP，不接收文件上传")


@app.get("/api/files")
def list_files():
    return file_store.list()


@app.get("/api/files/stats")
def file_stats():
    return file_store.stats()


@app.get("/api/files/{file_id}/preview")
def preview_file(
    file_id: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=200),
    line: int | None = Query(default=None, ge=1),
):
    try:
        return file_store.read_artifact_preview(file_id, offset=offset, limit=limit, line=line)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件已被移除") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/files/{file_id}/preview-index")
def preview_index_status(file_id: str):
    try:
        return file_store.preview_index_status(file_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="文件不存在") from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="文件已被移除") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/local/archives")
def scan_local_archives(request: LocalArchiveRequest):
    directory = Path(request.directory).expanduser().resolve()
    if not directory.exists() or not directory.is_dir():
        raise HTTPException(status_code=400, detail="本地目录不存在或不是文件夹")
    if request.scope not in {"month", "year"}:
        raise HTTPException(status_code=400, detail="scope 只能是 month 或 year")
    path_kind = "year" if directory.name.strip().isdigit() and len(directory.name.strip()) == 4 else "month" if MONTH_DIRECTORY_RE.fullmatch(directory.name.strip()) else "unknown"
    effective_directory = directory
    selected_month_directories = [Path(item).expanduser().resolve() for item in request.selected_month_directories]
    if request.selected_month_directory and not selected_month_directories:
        selected_month_directories = [Path(request.selected_month_directory).expanduser().resolve()]
    if request.scope == "month" and path_kind == "year":
        if not selected_month_directories:
            month_directories = sorted(
                (
                    item for item in directory.iterdir()
                    if item.is_dir() and MONTH_DIRECTORY_RE.fullmatch(item.name.strip())
                ),
                key=lambda item: int(item.name.strip()[:-1]),
            )
            return {
                "directory": str(directory),
                "effective_directory": str(directory),
                "scope": request.scope,
                "count": 0,
                "archives": [],
                "month_directories": [str(item) for item in month_directories],
                "message": "当前是年度目录，请选择具体月份目录后再扫描",
            }
        if any(item.parent != directory or not item.is_dir() or not MONTH_DIRECTORY_RE.fullmatch(item.name.strip()) for item in selected_month_directories):
            raise HTTPException(status_code=400, detail="所选月份目录必须是当前年度目录下的月份文件夹")
        selected_month_directories.sort(key=lambda item: int(item.name.strip()[:-1]))
        effective_directory = selected_month_directories[0]
        candidates = (path for month_directory in selected_month_directories for path in month_directory.glob("*.zip"))
    else:
        candidates = effective_directory.glob("*.zip") if request.scope == "month" else effective_directory.glob("*.zip") if path_kind == "month" else effective_directory.rglob("*.zip")
    archives = []
    seen = set()
    for path in sorted(candidates, key=_archive_sort_key):
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        archive = file_store.register_local_path(path)
        archive["month_directory"] = archive_month_directory(path, directory)
        archives.append(archive)
    return {
        "directory": str(directory),
        "effective_directory": str(effective_directory),
        "scope": request.scope,
        "count": len(archives),
        "archives": archives,
        "month_directories": [],
        "selected_month_directories": [str(item) for item in selected_month_directories],
        "message": "月份目录按年模式仍只处理当前月份" if path_kind == "month" and request.scope == "year" else "",
    }


@app.post("/api/local/pick-directory")
def pick_local_directory(request: Request, payload: LocalDirectoryPickerRequest):
    # This dialog belongs to the machine running FastAPI, so remote browsers must not trigger it.
    client_host = request.client.host if request.client else ""
    if client_host not in {"127.0.0.1", "::1", "localhost", "testclient"}:
        raise HTTPException(status_code=403, detail="原生目录选择仅允许本机调用")
    if os.name != "nt":
        raise HTTPException(status_code=501, detail="原生目录选择仅支持 Windows，请手动填写目录")
    try:
        import tkinter
        from tkinter import filedialog

        root = tkinter.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        try:
            initial_directory = Path(payload.initial_directory).expanduser()
            if not initial_directory.is_dir():
                initial_directory = Path.home()
            selected_directory = filedialog.askdirectory(
                parent=root,
                title="选择 ZIP 数据文件夹",
                initialdir=str(initial_directory),
                mustexist=True,
            )
        finally:
            root.destroy()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"无法打开 Windows 文件夹窗口: {exc}") from exc
    return {"directory": str(Path(selected_directory).resolve()) if selected_directory else ""}


@app.post("/api/tasks/extract")
def create_extract_task(request: ExtractRequest):
    if not request.file_ids and not request.source_directory:
        raise HTTPException(status_code=400, detail="请选择 ZIP 文件或填写本地目录")
    try:
        source_names = []
        for file_id in request.file_ids:
            source_names.append(file_store.get(file_id)["name"])
        if request.source_directory:
            directory = Path(request.source_directory).expanduser()
            if not directory.exists() or not directory.is_dir():
                raise ValueError("本地目录不存在或不是文件夹")
            source_name = str(directory)
        elif len(source_names) == 1:
            source_name = source_names[0]
        else:
            source_name = f"{len(source_names)} 个 ZIP 文件"
    except (KeyError, FileNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=404, detail=f"处理源不存在: {exc}") from exc
    return task_manager.create(
        "extract",
        {**request.model_dump(), "display_name": source_name},
        build_extract_worker(file_store),
    )


@app.post("/api/tasks/validate")
def create_validate_task(request: FileTaskRequest):
    try:
        item = file_store.get(request.file_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=f"文件不存在: {exc}") from exc
    return task_manager.create(
        "validate",
        {**request.model_dump(), "display_name": item["name"]},
        build_validate_worker(file_store),
    )


@app.post("/api/tasks/import")
def create_import_task(request: ImportRequest):
    try:
        item = file_store.get(request.file_id)
    except (KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=404, detail=f"文件不存在: {exc}") from exc
    return task_manager.create(
        "import",
        {**request.model_dump(), "display_name": item["name"]},
        build_import_worker(file_store, load_database_config),
    )


@app.get("/api/tasks")
def list_tasks():
    return task_manager.list()


@app.delete("/api/history")
def clear_history():
    try:
        task_result = task_manager.clear_history()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    file_result = file_store.clear_managed_files()
    return {**task_result, **file_result}


@app.get("/api/tasks/{task_id}")
def get_task(task_id: str):
    try:
        return task_manager.snapshot(task_id, include_events=True)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


@app.get("/api/tasks/{task_id}/events/history")
def task_event_history(
    task_id: str,
    before_id: int | None = Query(default=None, ge=1),
    after_id: int | None = Query(default=None, ge=0),
    limit: int = Query(default=500, ge=1, le=500),
):
    try:
        return task_manager.event_page(
            task_id, before_id=before_id, after_id=after_id, limit=limit
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc


@app.post("/api/tasks/{task_id}/pause")
def pause_task(task_id: str):
    try:
        return task_manager.pause(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/tasks/{task_id}/resume")
def resume_task(task_id: str):
    try:
        return task_manager.resume(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/tasks/{task_id}/events")
def task_events(
    task_id: str,
    cursor: int = 0,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
):
    try:
        task_manager.snapshot(task_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="任务不存在") from exc
    try:
        event_cursor = max(cursor, int(last_event_id or 0))
    except ValueError:
        event_cursor = cursor
    return StreamingResponse(
        task_manager.event_stream(task_id, event_cursor),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
