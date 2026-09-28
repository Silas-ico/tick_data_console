import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterator

import pymysql

from ..storage import FileStore
from ..task_manager import TaskContext


IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_identifier(value: str, label: str) -> str:
    value = value.strip()
    if not IDENTIFIER_RE.fullmatch(value):
        raise ValueError(f"{label}只能包含字母、数字和下划线")
    return value


def detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:65536]
    for encoding in ("utf-8-sig", "gbk", "gb2312"):
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin1"


def iter_lines(path: Path, encoding: str) -> Iterator[str]:
    with path.open("r", encoding=encoding, errors="replace") as source:
        yield from source


def count_lines(path: Path) -> int:
    count = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            count += chunk.count(b"\n")
    return max(count, 1)


def parse_line(line: str, line_num: int) -> tuple[dict | None, str | None]:
    try:
        start_index = line.find("TICK_DATA:{")
        if start_index == -1:
            if "{" in line and "}" in line:
                start_index = line.find("{")
            else:
                return None, "未找到 TICK_DATA 标记或 JSON 数据"

        json_part = line[start_index:]
        brace_start = json_part.find("{")
        brace_count = 0
        end_position = -1
        in_string = False
        escaped = False
        for index, character in enumerate(json_part[brace_start:]):
            if escaped:
                escaped = False
                continue
            if character == "\\" and in_string:
                escaped = True
                continue
            if character == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if character == "{":
                brace_count += 1
            elif character == "}":
                brace_count -= 1
                if brace_count == 0:
                    end_position = brace_start + index + 1
                    break
        if end_position == -1:
            return None, "未找到匹配的 JSON 结束大括号"

        json_text = json_part[brace_start:end_position]
        json_text = re.sub(
            r"([a-zA-Z_][a-zA-Z0-9_]*)\s*:",
            lambda match: f'"{match.group(1).strip()}":',
            json_text,
        )
        try:
            return json.loads(json_text), None
        except json.JSONDecodeError:
            data = {}
            content = json_text[1:-1].strip()
            pairs = []
            current = ""
            quoted = False
            for character in content:
                if character == '"' and (not current or current[-1] != "\\"):
                    quoted = not quoted
                if character == "," and not quoted:
                    pairs.append(current.strip())
                    current = ""
                    continue
                current += character
            if current:
                pairs.append(current.strip())
            for pair in pairs:
                if ":" not in pair:
                    continue
                key_part, value_part = pair.split(":", 1)
                key = key_part.strip().strip('"')
                value_text = value_part.strip()
                if value_text.startswith('"') and value_text.endswith('"'):
                    value = value_text[1:-1]
                elif value_text == "null":
                    value = None
                else:
                    try:
                        value = float(value_text) if "." in value_text else int(value_text)
                    except ValueError:
                        value = value_text
                data[key] = value
            if not data:
                return None, f"第 {line_num} 行 JSON 无法解析"
            return data, None
    except Exception as exc:
        return None, f"第 {line_num} 行解析异常: {exc}"


class TimestampAdjuster:
    def __init__(self, enabled: bool = True) -> None:
        self.enabled = enabled
        self.processed: dict[tuple[str, datetime], list[int]] = {}

    def adjust(self, code: str, original: datetime) -> tuple[datetime, bool, int]:
        if not self.enabled:
            return original, False, 0
        key = (code, original.replace(microsecond=0))
        offsets = self.processed.get(key)
        if offsets is None:
            self.processed[key] = [original.microsecond]
            return original, False, 0

        used = sorted(offsets)
        candidate = max(original.microsecond, 1000)
        while candidate in used and candidate <= 500000:
            candidate += 1000
        if candidate > 500000:
            candidate = min(999999, used[-1] + 1)
        adjusted = original.replace(microsecond=0) + timedelta(microseconds=candidate)
        self.processed[key].append(candidate)
        return adjusted, True, max(0, candidate - original.microsecond)


def _number(value, default=None, integer: bool = False):
    if value is None or str(value).strip() == "":
        return default
    try:
        return int(float(value)) if integer else float(value)
    except (TypeError, ValueError):
        return default


def convert_data(raw: dict, line_num: int, adjuster: TimestampAdjuster) -> tuple[dict | None, str | None]:
    code = str(raw.get("code", "")).strip()
    if not code:
        return None, f"第 {line_num} 行缺少合约代码"
    datetime_text = re.sub(r"\s+", " ", str(raw.get("datetime", "")).strip())
    if not datetime_text:
        return None, f"第 {line_num} 行缺少时间戳"
    try:
        if "." in datetime_text:
            main_time, microseconds = datetime_text.split(".", 1)
            digits = "".join(filter(str.isdigit, microseconds))[:6].ljust(6, "0")
            original_time = datetime.strptime(f"{main_time}.{digits}", "%Y-%m-%d %H:%M:%S.%f")
        else:
            original_time = datetime.strptime(datetime_text, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        return None, f"第 {line_num} 行时间格式错误: {exc}"

    tick_time, adjusted, adjustment = adjuster.adjust(code, original_time)
    data = {
        "code": code,
        "tick_time": tick_time,
        "current_price": _number(raw.get("current")),
        "open_price": _number(raw.get("open")),
        "high_price": _number(raw.get("high")),
        "low_price": _number(raw.get("low")),
        "volume": _number(raw.get("volume"), 0, integer=True),
        "money": _number(raw.get("money"), 0.0),
        "position": _number(raw.get("position"), 0.0),
        "ask1_price": _number(raw.get("ask1_price")),
        "ask1_volume": _number(raw.get("ask1_volume"), 0, integer=True),
        "bid1_price": _number(raw.get("bid1_price")),
        "bid1_volume": _number(raw.get("bid1_volume"), 0, integer=True),
        "spread": _number(raw.get("spread")),
        "mid_price": _number(raw.get("mid_price")),
        "underlying_symbol": str(raw.get("underlying_symbol", "HC")).strip(),
        "exchange": str(raw.get("exchange", "XSGE")).strip(),
        "original_tick_id": _number(raw.get("tick_id"), 0, integer=True),
        "total_ticks": _number(raw.get("total_ticks"), 0, integer=True),
        "time_adjusted": adjusted,
        "adjustment_microseconds": adjustment,
    }
    log_timestamp = raw.get("log_timestamp")
    try:
        data["log_timestamp"] = datetime.strptime(str(log_timestamp), "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        data["log_timestamp"] = datetime.now()
    return data, None


def quality_issues(data: dict) -> list[str]:
    issues = []
    if data["current_price"] is None:
        issues.append("最新价为空")
    if data["volume"] < 0:
        issues.append("成交量为负数")
    if data["ask1_price"] is not None and data["bid1_price"] is not None:
        if data["ask1_price"] < data["bid1_price"]:
            issues.append("卖一价小于买一价")
    if data["high_price"] is not None and data["low_price"] is not None:
        if data["high_price"] < data["low_price"]:
            issues.append("最高价小于最低价")
    return issues


def build_validate_worker(file_store: FileStore):
    def worker(context: TaskContext, payload: dict) -> dict:
        item = file_store.get(payload["file_id"])
        path = item["resolved_path"]
        encoding = detect_encoding(path)
        total_lines = count_lines(path)
        adjuster = TimestampAdjuster(bool(payload.get("adjust_duplicates", True)))
        counters = Counter()
        samples = []
        preview = []
        context.log(f"使用 {encoding} 编码检查文件: {item['name']}")

        for line_num, line in enumerate(iter_lines(path, encoding), start=1):
            stripped = line.strip()
            if not stripped:
                counters["empty_lines"] += 1
                continue
            raw, parse_error = parse_line(stripped, line_num)
            if parse_error:
                counters["invalid_lines"] += 1
                counters["JSON/日志格式错误"] += 1
                if len(samples) < 100:
                    samples.append({
                        "line": line_num,
                        "type": "解析错误",
                        "message": parse_error,
                        "content": stripped[:300],
                    })
                continue
            data, convert_error = convert_data(raw, line_num, adjuster)
            if convert_error:
                counters["invalid_lines"] += 1
                counters["字段错误"] += 1
                if len(samples) < 100:
                    samples.append({
                        "line": line_num,
                        "type": "字段错误",
                        "message": convert_error,
                        "content": stripped[:300],
                    })
                continue
            counters["valid_lines"] += 1
            if data["time_adjusted"]:
                counters["timestamp_adjustments"] += 1
            issues = quality_issues(data)
            for issue in issues:
                counters[issue] += 1
                if len(samples) < 100:
                    samples.append({
                        "line": line_num,
                        "type": "质量警告",
                        "message": issue,
                        "content": stripped[:300],
                    })
            if len(preview) < 10:
                preview.append({
                    key: str(value) if isinstance(value, datetime) else value
                    for key, value in data.items()
                })
            if line_num % 500 == 0:
                context.progress(line_num / total_lines * 98, f"已检查 {line_num}/{total_lines} 行")

        issue_counts = {
            key: value
            for key, value in counters.items()
            if key not in {"valid_lines", "invalid_lines", "empty_lines", "timestamp_adjustments"}
        }
        context.log(
            f"检查完成：有效 {counters['valid_lines']} 行，无效 {counters['invalid_lines']} 行，"
            f"警告 {sum(issue_counts.values())} 项"
        )
        return {
            "file": FileStore.public_item(item),
            "encoding": encoding,
            "total_lines": total_lines,
            "valid_lines": counters["valid_lines"],
            "invalid_lines": counters["invalid_lines"],
            "empty_lines": counters["empty_lines"],
            "timestamp_adjustments": counters["timestamp_adjustments"],
            "issue_counts": issue_counts,
            "samples": samples,
            "preview": preview,
        }

    return worker


def _server_connection(config: dict):
    return pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["user"],
        password=config.get("password", ""),
        charset=config.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8,
    )


def ensure_database(config: dict) -> None:
    database = validate_identifier(config["database"], "数据库名")
    connection = _server_connection(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                f"CREATE DATABASE IF NOT EXISTS `{database}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        connection.commit()
    finally:
        connection.close()


def connect_database(config: dict):
    return pymysql.connect(
        host=config["host"],
        port=int(config["port"]),
        user=config["user"],
        password=config.get("password", ""),
        database=validate_identifier(config["database"], "数据库名"),
        charset=config.get("charset", "utf8mb4"),
        cursorclass=pymysql.cursors.DictCursor,
        connect_timeout=8,
        autocommit=False,
    )


def test_database(config: dict) -> dict:
    ensure_database(config)
    connection = connect_database(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT VERSION() AS version, DATABASE() AS database_name")
            return cursor.fetchone()
    finally:
        connection.close()


def list_tick_tables(config: dict) -> list[dict]:
    # Database existence is validated by connect_database; avoid a second connection.
    connection = connect_database(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW TABLES")
            rows = cursor.fetchall()
        tables = []
        for row in rows:
            table_name = str(next(iter(row.values())))
            if not table_name.lower().startswith("tick_data_"):
                continue
            code = table_name[len("tick_data_"):].strip().upper()
            if not code:
                continue
            tables.append({"name": table_name, "code": code, "label": f"{code} · {table_name}"})
        return sorted(tables, key=lambda item: item["code"])
    finally:
        connection.close()



def _fast_table_summary(connection, table_name: str) -> dict:
    """Use cheap MySQL metadata for an unfiltered table browse."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT TABLE_ROWS AS total FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s",
            (table_name,),
        )
        row = cursor.fetchone() or {}
        total = int(row.get("total") or 0)
        quoted = chr(96) + table_name + chr(96)
        cursor.execute("SELECT tick_time AS value FROM " + quoted + " WHERE tick_time IS NOT NULL ORDER BY tick_time ASC LIMIT 1")
        min_row = cursor.fetchone() or {}
        cursor.execute("SELECT tick_time AS value FROM " + quoted + " WHERE tick_time IS NOT NULL ORDER BY tick_time DESC LIMIT 1")
        max_row = cursor.fetchone() or {}
    return {"total": total, "min_time": min_row.get("value"), "max_time": max_row.get("value")}

def table_exists(connection, table_name: str) -> bool:
    table = validate_identifier(table_name, "表名")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = DATABASE() AND table_name = %s LIMIT 1",
            (table,),
        )
        return cursor.fetchone() is not None


def infer_product_from_file(path: Path, encoding: str) -> str:
    for line_number, line in enumerate(iter_lines(path, encoding), start=1):
        raw, parse_error = parse_line(line.strip(), line_number)
        if parse_error or not raw:
            continue
        symbol = str(raw.get("underlying_symbol", "")).strip()
        if symbol:
            return symbol.upper()
        code = str(raw.get("code", "")).strip().split(".", 1)[0]
        letters = "".join(character for character in code if character.isalpha())
        if letters:
            return letters.upper()
    return ""


INSERT_COLUMNS = (
    "code", "tick_time", "current_price", "open_price", "high_price", "low_price",
    "volume", "money", "position", "ask1_price", "ask1_volume", "bid1_price",
    "bid1_volume", "spread", "mid_price", "underlying_symbol", "exchange",
    "log_timestamp", "original_tick_id", "total_ticks",
)


def _parse_query_datetime(value: str | None, label: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label}格式错误，请使用 YYYY-MM-DD 或 YYYY-MM-DDTHH:MM") from exc
    return parsed.replace(tzinfo=None)


def query_tick_records(
    config: dict,
    table_name: str | None = None,
    keyword: str | None = None,
    code: str | None = None,
    start_time: str | None = None,
    end_time: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    page = max(1, int(page))
    page_size = min(200, max(1, int(page_size)))
    start = _parse_query_datetime(start_time, "开始时间")
    end = _parse_query_datetime(end_time, "结束时间")
    if start and end and start > end:
        raise ValueError("开始时间不能晚于结束时间")

    available_tables = list_tick_tables(config)
    table_lookup = {item["name"].lower(): item for item in available_tables}
    requested_table = str(table_name or "").strip()
    if requested_table:
        selected_table = table_lookup.get(requested_table.lower())
        if not selected_table:
            raise ValueError("只能查询本地数据库中已发现的 tick_data_% 表")
        selected_tables = [selected_table]
    else:
        selected_tables = available_tables

    keyword_text = str(keyword or "").strip()
    code_text = str(code or "").strip()
    connection = connect_database(config)
    groups = []
    try:
        for table in selected_tables:
            safe_table = validate_identifier(table["name"], "表名")
            conditions = ["1 = 1"]
            params = []
            if code_text:
                conditions.append("`code` = %s")
                params.append(code_text)
            if keyword_text:
                conditions.append("(`code` LIKE %s OR `underlying_symbol` LIKE %s OR `exchange` LIKE %s)")
                pattern = f"%{keyword_text}%"
                params.extend([pattern, pattern, pattern])
            if start:
                conditions.append("`tick_time` >= %s")
                params.append(start)
            if end:
                conditions.append("`tick_time` <= %s")
                params.append(end)
            where_clause = " AND ".join(conditions)

            if not (code_text or keyword_text or start or end):
                summary = _fast_table_summary(connection, safe_table)
            else:
                with connection.cursor() as summary_cursor:
                    summary_cursor.execute(
                        "SELECT COUNT(*) AS total, MIN(tick_time) AS min_time, "
                        "MAX(tick_time) AS max_time FROM " + safe_table + " WHERE " + where_clause,
                        params,
                    )
                    summary = summary_cursor.fetchone() or {"total": 0, "min_time": None, "max_time": None}
            with connection.cursor() as cursor:
                offset = (page - 1) * page_size
                query_columns = ", ".join(f"`{column}`" for column in INSERT_COLUMNS)
                cursor.execute(
                    f"SELECT `id`, {query_columns} FROM `{safe_table}` WHERE {where_clause} "
                    "ORDER BY `tick_time` DESC, `id` DESC LIMIT %s OFFSET %s",
                    [*params, page_size, offset],
                )
                rows = cursor.fetchall()

            total = int(summary.get("total") or 0)
            groups.append({
                **table,
                "total": total,
                "min_time": summary.get("min_time"),
                "max_time": summary.get("max_time"),
                "page": page,
                "page_size": page_size,
                "has_next": offset + len(rows) < total,
                "rows": rows,
            })
    finally:
        connection.close()

    total = sum(group["total"] for group in groups)
    page_count = max(
        ((group["total"] + page_size - 1) // page_size for group in groups),
        default=0,
    )
    return {
        "database": config["database"],
        "table_name": requested_table,
        "keyword": keyword_text,
        "code": code_text,
        "start_time": start_time or "",
        "end_time": end_time or "",
        "page": page,
        "page_size": page_size,
        "page_count": page_count,
        "total": total,
        "groups": groups,
    }


def build_import_worker(file_store: FileStore, database_config_loader):
    def worker(context: TaskContext, payload: dict) -> dict:
        item = file_store.get(payload["file_id"])
        path = item["resolved_path"]
        table_name = validate_identifier(payload["table_name"], "表名")
        config = database_config_loader()
        context.log(f"连接本地数据库 {config['host']}:{config['port']}/{config['database']}")
        ensure_database(config)
        connection = connect_database(config)
        encoding = detect_encoding(path)
        total_lines = count_lines(path)
        adjuster = TimestampAdjuster(bool(payload.get("adjust_duplicates", True)))
        stats = Counter()
        errors = []
        placeholders = ", ".join(["%s"] * len(INSERT_COLUMNS))
        quoted_columns = ", ".join(f"`{column}`" for column in INSERT_COLUMNS)
        sql = f"INSERT IGNORE INTO `{table_name}` ({quoted_columns}) VALUES ({placeholders})"

        try:
            if not table_exists(connection, table_name):
                raise ValueError(f"目标数据表不存在: {table_name}。请先在本地数据库创建对应表")
            file_product = infer_product_from_file(path, encoding)
            table_product = table_name[len("tick_data_"):].upper() if table_name.lower().startswith("tick_data_") else ""
            if file_product and table_product and file_product != table_product:
                raise ValueError(
                    f"日志文件品种与目标表不匹配: 文件={file_product}，目标表={table_name}"
                )
            context.log(f"确认目标数据表存在且匹配: {table_name}")
            with connection.cursor() as cursor:
                for line_num, line in enumerate(iter_lines(path, encoding), start=1):
                    stripped = line.strip()
                    if not stripped:
                        stats["empty_lines"] += 1
                        continue
                    raw, parse_error = parse_line(stripped, line_num)
                    if parse_error:
                        stats["failed_parse"] += 1
                        if len(errors) < 100:
                            errors.append({"line": line_num, "message": parse_error})
                        continue
                    data, convert_error = convert_data(raw, line_num, adjuster)
                    if convert_error:
                        stats["failed_parse"] += 1
                        if len(errors) < 100:
                            errors.append({"line": line_num, "message": convert_error})
                        continue
                    values = tuple(data.get(column) for column in INSERT_COLUMNS)
                    try:
                        cursor.execute(sql, values)
                        if cursor.rowcount:
                            stats["inserted"] += 1
                        else:
                            stats["duplicates"] += 1
                        if data["time_adjusted"]:
                            stats["timestamp_adjustments"] += 1
                    except pymysql.MySQLError as exc:
                        stats["failed_insert"] += 1
                        if len(errors) < 100:
                            errors.append({"line": line_num, "message": str(exc)})
                    if line_num % 500 == 0:
                        connection.commit()
                        context.progress(line_num / total_lines * 98, f"已导入 {line_num}/{total_lines} 行")
                    if line_num % 2000 == 0:
                        context.log(
                            f"进度 {line_num}/{total_lines}，新增 {stats['inserted']}，"
                            f"重复 {stats['duplicates']}，失败 {stats['failed_parse'] + stats['failed_insert']}"
                        )
            connection.commit()
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS count, MIN(tick_time) AS min_time, "
                    f"MAX(tick_time) AS max_time FROM `{table_name}`"
                )
                database_stats = cursor.fetchone()
            context.log(f"导入完成，成功新增 {stats['inserted']} 条，跳过重复 {stats['duplicates']} 条")
            return {
                "file": FileStore.public_item(item),
                "database": config["database"],
                "table": table_name,
                "encoding": encoding,
                "total_lines": total_lines,
                "inserted": stats["inserted"],
                "duplicates": stats["duplicates"],
                "failed_parse": stats["failed_parse"],
                "failed_insert": stats["failed_insert"],
                "empty_lines": stats["empty_lines"],
                "timestamp_adjustments": stats["timestamp_adjustments"],
                "errors": errors,
                "database_stats": {
                    key: str(value) if value is not None else None
                    for key, value in database_stats.items()
                },
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    return worker
