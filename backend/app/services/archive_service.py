import html
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import EXTRACT_DIR
from ..storage import FileStore
from ..task_manager import TaskContext
from .tick_service import TimestampAdjuster, convert_data, parse_line, quality_issues


START_KEY = "STATS:已采集天数"
END_KEY = "取消已订阅的所有标的"
MAX_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
MONTH_DIRECTORY_RE = re.compile(r"^(?:0?[1-9]|1[0-2])月$")
YEAR_DIRECTORY_RE = re.compile(r"^\d{4}$")


def _safe_destination(root: Path, member_name: str) -> Path:
    destination = (root / member_name).resolve()
    if root.resolve() not in destination.parents and destination != root.resolve():
        raise ValueError(f"压缩包包含不安全路径: {member_name}")
    return destination


def _extract_middle(lines: list[str]) -> tuple[list[str], str | None]:
    start_index = next(
        (index for index, line in enumerate(lines) if START_KEY.lower() in line.lower()),
        None,
    )
    end_index = next(
        (index for index in range(len(lines) - 1, -1, -1) if END_KEY.lower() in lines[index].lower()),
        None,
    )
    if start_index is None:
        return [], f"未找到开始标记“{START_KEY}”"
    if end_index is None:
        return [], f"未找到结束标记“{END_KEY}”"
    if start_index >= end_index:
        return [], "开始标记位于结束标记之后"
    return lines[start_index + 1 : end_index], None


def _product_from_record(record: dict) -> str:
    symbol = str(record.get("underlying_symbol", "")).strip()
    if symbol:
        return symbol.upper()
    code = str(record.get("code", "")).strip()
    match = code.split(".", 1)[0]
    letters = "".join(character for character in match if character.isalpha())
    return letters.upper()


def _matches_product(record: dict, target_product: str) -> bool:
    target = target_product.strip().upper()
    if not target:
        return True
    product = _product_from_record(record)
    code = str(record.get("code", "")).strip().upper()
    contract = code.split(".", 1)[0]
    symbol = str(record.get("underlying_symbol", "")).strip().upper()
    commodity_name = str(record.get("commodity_name", "")).strip()
    return target in {product, symbol, code, contract} or commodity_name == target_product.strip()


def _directory_kind(directory: Path) -> str:
    name = directory.name.strip()
    if MONTH_DIRECTORY_RE.fullmatch(name):
        return "month"
    if YEAR_DIRECTORY_RE.fullmatch(name):
        return "year"
    return "unknown"


def _directory_product(directory: Path) -> str:
    parts = list(directory.parts)
    year_index = next((index for index, part in enumerate(parts) if YEAR_DIRECTORY_RE.fullmatch(part.strip())), None)
    if year_index is None or year_index == 0:
        return ""
    candidate = parts[year_index - 1].strip()
    return candidate.upper() if re.fullmatch(r"[A-Za-z]{1,3}", candidate) else ""


def _archive_period_label(path: Path) -> str:
    """Return the year/month represented by a local archive's parent folder."""
    parts = path.parts
    for index in range(len(parts) - 2, -1, -1):
        month_name = parts[index].strip()
        if not MONTH_DIRECTORY_RE.fullmatch(month_name):
            continue
        year_name = parts[index - 1].strip() if index else ""
        if YEAR_DIRECTORY_RE.fullmatch(year_name):
            return f"{year_name}年{int(month_name[:-1])}月"
        return f"{int(month_name[:-1])}月"
    return ""


def archive_month_directory(archive_path: Path, scan_directory: Path) -> str | None:
    """Find the nearest month folder below the scanned directory for UI labels."""
    scan_directory = scan_directory.resolve()
    resolved_archive = archive_path.resolve()
    for candidate in (resolved_archive.parent, *resolved_archive.parents):
        if MONTH_DIRECTORY_RE.fullmatch(candidate.name.strip()) and (
            candidate == scan_directory or scan_directory in candidate.parents
        ):
            return candidate.name.strip()
        if candidate == scan_directory:
            break
    return None


def _archive_sort_key(path: Path) -> tuple:
    """Sort local archives by the nearest month/year folders before filename."""
    parts = path.parts
    for index in range(len(parts) - 2, -1, -1):
        month_name = parts[index].strip()
        if not MONTH_DIRECTORY_RE.fullmatch(month_name):
            continue
        year_name = parts[index - 1].strip() if index else ""
        year = int(year_name) if YEAR_DIRECTORY_RE.fullmatch(year_name) else 0
        return year, int(month_name[:-1]), str(path).lower()
    return 9999, 99, str(path).lower()


def _default_output_name(directory: str | None, scope: str, target_product: str, selected_month_directories=None) -> str:
    if directory:
        path = Path(directory)
        product = _directory_product(path) or target_product.strip().upper()
        year = next((part for part in path.parts if YEAR_DIRECTORY_RE.fullmatch(part.strip())), "")
        months = sorted({
            int(Path(item).name.strip()[:-1])
            for item in selected_month_directories or []
            if MONTH_DIRECTORY_RE.fullmatch(Path(item).name.strip())
        })
        month_match = MONTH_DIRECTORY_RE.fullmatch(path.name.strip())
        if not months and month_match:
            months = [int(path.name.strip()[:-1])]
        if product and year and months:
            month_label = "_".join(f"{month:02d}" for month in months)
            return f"{product}_{year}_{month_label}.txt"
        if product and year and scope == "year":
            return f"{product}_{year}.txt"
    return f"merged_log_{datetime.now():%Y%m%d_%H%M%S}.txt"


def _register_local_files(file_store: FileStore, paths) -> list[dict]:
    files = []
    seen = set()
    for path in sorted(paths, key=_archive_sort_key):
        resolved = path.resolve()
        if resolved in seen or not path.is_file():
            continue
        seen.add(resolved)
        file_store.register_local_path(path)
        files.append({
            "id": str(resolved),
            "name": path.name,
            "extension": ".zip",
            "resolved_path": resolved,
        })
    return files


def _source_files(file_store: FileStore, payload: dict) -> tuple[list[dict], str, str | None]:
    source_directory = str(payload.get("source_directory") or "").strip()
    if source_directory:
        directory = Path(source_directory).expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"本地目录不存在: {directory}")
        scope = payload.get("merge_scope", "month")
        path_kind = _directory_kind(directory)
        selected_months = [str(item).strip() for item in payload.get("selected_month_directories") or [] if str(item).strip()]
        selected_month = str(payload.get("selected_month_directory") or "").strip()
        if selected_month and not selected_months:
            selected_months = [selected_month]
        if scope == "month" and path_kind == "year":
            if not selected_months:
                raise ValueError("当前是年度目录，请先选择具体月份目录")
            month_directories = sorted(
                (Path(item).expanduser().resolve() for item in selected_months),
                key=lambda item: int(item.name.strip()[:-1]),
            )
            if any(not item.is_dir() or item.parent != directory or _directory_kind(item) != "month" for item in month_directories):
                raise ValueError("所选月份目录必须是当前年度目录下的月份文件夹")
            paths = (path for month_directory in month_directories for path in month_directory.glob("*.zip"))
            selected_label = "、".join(item.name for item in month_directories)
            effective_directory = month_directories[0] if len(month_directories) == 1 else directory
            return _register_local_files(file_store, paths), str(effective_directory), f"年度目录已选择月份 {selected_label}，仅处理选中的月份"
        if scope == "year" and path_kind == "month":
            return _register_local_files(file_store, directory.glob("*.zip")), str(directory), "当前填写的是月份目录，按年模式不会扩大到上级年度目录，仅处理当前月份"
        paths = directory.glob("*.zip") if scope == "month" else directory.rglob("*.zip")
        return _register_local_files(file_store, paths), str(directory), None

    file_ids = payload.get("file_ids") or []
    return [file_store.get(file_id) for file_id in file_ids], "", None


def _find_log_range(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> tuple[int | None, int | None]:
    first_start = None
    last_end = None
    with archive.open(member, "r") as source:
        text = io.TextIOWrapper(source, encoding="gbk", errors="replace")
        for line_number, line in enumerate(text):
            value = line.strip()
            if first_start is None and START_KEY.lower() in value.lower():
                first_start = line_number
            if END_KEY.lower() in value.lower():
                last_end = line_number
    return first_start, last_end


def build_extract_worker(file_store: FileStore):
    def worker(context: TaskContext, payload: dict) -> dict:
        files, effective_directory, scope_note = _source_files(file_store, payload)
        if not files:
            raise ValueError("指定目录中没有 ZIP 文件")
        invalid = [item["name"] for item in files if item["extension"] != ".zip"]
        if invalid:
            raise ValueError(f"仅支持 ZIP 文件: {', '.join(invalid)}")

        extract_root = EXTRACT_DIR / context.task_id
        extract_root.mkdir(parents=True, exist_ok=True)
        temporary_output = extract_root / "merged_log.txt"
        total_zip_files = len(files)
        total_members = 0
        total_log_files = 0
        total_written_lines = 0
        total_rejected_lines = 0
        product_mismatch_lines = 0
        issues = []

        context.log(f"开始处理：共 {total_zip_files:,} 个压缩包")
        if scope_note:
            context.log(scope_note)
        rejected_output = extract_root / "merged_log_rejected.txt"
        with (
            temporary_output.open("w", encoding="utf-8") as output,
            rejected_output.open("w", encoding="utf-8") as rejected,
        ):
            target_product = str(payload.get("target_product") or "").strip()
            validator = TimestampAdjuster(enabled=False)
            if target_product:
                context.log(f"品种筛选已启用：仅保留 {target_product}")
            else:
                context.log("未设置目标品种，将仅检查数据格式", level="warning")
            for file_index, item in enumerate(files, start=1):
                context.wait_if_paused()
                zip_path = item["resolved_path"]
                period_label = _archive_period_label(zip_path)
                period_suffix = f"（{period_label}）" if period_label else ""
                context.log(f"正在处理压缩包{period_suffix}：{item['name']}")
                with zipfile.ZipFile(zip_path, "r") as archive:
                    members = archive.infolist()
                    total_size = sum(member.file_size for member in members)
                    if total_size > MAX_UNCOMPRESSED_BYTES:
                        raise ValueError(f"ZIP 内文件展开后的总大小超过 2GB 限制: {item['name']}")

                    for member in members:
                        if member.is_dir():
                            continue
                        total_members += 1
                        if not member.filename.lower().endswith("log.txt"):
                            continue

                        total_log_files += 1
                        first_start, last_end = _find_log_range(archive, member)
                        if first_start is None:
                            issue = f"未找到开始标记“{START_KEY}”"
                        elif last_end is None:
                            issue = f"未找到结束标记“{END_KEY}”"
                        elif first_start >= last_end:
                            issue = "开始标记位于结束标记之后"
                        else:
                            issue = None
                        if issue:
                            issues.append({"zip": item["name"], "member": member.filename, "issue": issue})
                            context.log(f"{member.filename}: {issue}", level="warning")
                            continue
                        # Stream each ZIP member directly instead of expanding archives to disk.
                        with archive.open(member, "r") as source:
                            text = io.TextIOWrapper(source, encoding="gbk", errors="replace")
                            for line_number, line in enumerate(text):
                                if line_number <= first_start or line_number >= last_end:
                                    continue
                                cleaned_line = html.unescape(line).strip()
                                if "TICK_DATA" not in cleaned_line:
                                    total_rejected_lines += 1
                                    rejected.write(f"[非 TICK_DATA 行] {cleaned_line}\n")
                                    continue
                                record, parse_issue = parse_line(cleaned_line, line_number)
                                if parse_issue:
                                    total_rejected_lines += 1
                                    if len(issues) < 100:
                                        issues.append({
                                            "zip": item["name"],
                                            "member": member.filename,
                                            "issue": f"数据行格式错误: {parse_issue}",
                                        })
                                    rejected.write(f"[格式错误] {cleaned_line}\n")
                                    continue
                                validated, validation_issue = convert_data(record, line_number, validator)
                                if validation_issue:
                                    total_rejected_lines += 1
                                    rejected.write(f"[字段错误: {validation_issue}] {cleaned_line}\n")
                                    continue
                                data_issues = quality_issues(validated)
                                if data_issues:
                                    total_rejected_lines += 1
                                    rejected.write(
                                        f"[质量异常: {'；'.join(data_issues)}] {cleaned_line}\n"
                                    )
                                    continue
                                if not _matches_product(record, target_product):
                                    total_rejected_lines += 1
                                    product_mismatch_lines += 1
                                    actual_product = _product_from_record(record) or "未知"
                                    rejected.write(
                                        f"[品种不匹配: 目标={target_product or '未设置'}, 实际={actual_product}] "
                                        f"{cleaned_line}\n"
                                    )
                                    continue
                                output.write(cleaned_line + "\n")
                                total_written_lines += 1

                context.progress(
                    file_index / total_zip_files * 95,
                    f"已处理 {file_index:,} / {total_zip_files:,} 个压缩包",
                )
                context.checkpoint(completed_archives=file_index)
                context.wait_if_paused()

        output_name = payload.get("output_name") or _default_output_name(
            effective_directory,
            payload.get("merge_scope", "month"),
            target_product,
            payload.get("selected_month_directories"),
        )
        artifact = file_store.register_artifact(output_name, temporary_output)
        rejected_artifact = file_store.register_artifact(
            f"{Path(output_name).stem}_rejected.txt",
            rejected_output,
        )
        context.log(f"合并文件已生成：{artifact['name']}")
        context.log(f"筛除的数据已另存：{rejected_artifact['name']}")
        return {
            "artifact": artifact,
            "rejected_artifact": rejected_artifact,
            "extract_directory": str(extract_root.resolve()),
            "zip_files": total_zip_files,
            "extracted_files": total_members,
            "log_files": total_log_files,
            "written_lines": total_written_lines,
            "rejected_lines": total_rejected_lines,
            "product_mismatch_lines": product_mismatch_lines,
            "target_product": target_product,
            "source_directory": str(payload.get("source_directory") or ""),
            "effective_directory": effective_directory,
            "merge_scope": payload.get("merge_scope", "month"),
            "issues": issues[:100],
        }

    return worker
