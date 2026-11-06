import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from time import time
from typing import Iterator

import pymysql

from ..storage import FileStore
from ..task_manager import TaskContext
from ..config import DATA_DIR


IDENTIFIER_RE = re.compile(r"^[A-Za-z0-9_]+$")
COVERAGE_CACHE_TTL = 24 * 60 * 60
_coverage_cache_lock = RLock()
_coverage_year_cache: dict[tuple[str, str], tuple[float, list[dict]]] = {}
_coverage_cache_path = DATA_DIR / "coverage_cache.json"


def _load_coverage_cache() -> None:
    try:
        payload = json.loads(_coverage_cache_path.read_text(encoding="utf-8"))
        now = time()
        for key, value in payload.items():
            database, table = key.split("/", 1)
            cached_at = float(value["cached_at"])
            years = value["years"]
            if now - cached_at < COVERAGE_CACHE_TTL and isinstance(years, list):
                _coverage_year_cache[(database, table)] = (cached_at, years)
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return


def _persist_coverage_cache() -> None:
    payload = {
        f"{database}/{table}": {"cached_at": cached_at, "years": years}
        for (database, table), (cached_at, years) in _coverage_year_cache.items()
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with NamedTemporaryFile("w", encoding="utf-8", dir=DATA_DIR, delete=False) as temporary:
            json.dump(payload, temporary, ensure_ascii=False)
            temporary_path = Path(temporary.name)
        temporary_path.replace(_coverage_cache_path)
    except OSError:
        if temporary_path:
            temporary_path.unlink(missing_ok=True)


_load_coverage_cache()


def invalidate_coverage_cache(database: str, table_name: str) -> None:
    with _coverage_cache_lock:
        _coverage_year_cache.pop((database.lower(), table_name.lower()), None)
        _persist_coverage_cache()


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
        context.log(f"开始检查文件：{item['name']}（{encoding} 编码）")

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
                context.progress(
                    line_num / total_lines * 98,
                    f"已检查 {line_num:,} / {total_lines:,} 行",
                )

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


def query_tick_coverage(config: dict, table_name: str, year: int | None = None, month: int | None = None) -> dict:
    table = validate_identifier(str(table_name or ""), "表名")
    available_tables = list_tick_tables(config)
    table_lookup = {item["name"].lower(): item for item in available_tables}
    selected_table = table_lookup.get(table.lower())
    if not selected_table:
        raise ValueError("只能查询本地数据库中已发现的 tick_data_% 表")
    safe_table = validate_identifier(selected_table["name"], "表名")
    now = datetime.now()
    today = now.date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    year_start = date(today.year, 1, 1)
    next_year_start = date(today.year + 1, 1, 1)
    if month_start.month == 12:
        next_month_start = date(month_start.year + 1, 1, 1)
    else:
        next_month_start = date(month_start.year, month_start.month + 1, 1)

    connection = connect_database(config)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM information_schema.statistics "
                "WHERE table_schema = DATABASE() AND table_name = %s AND column_name = 'tick_time'",
                (safe_table,),
            )
            if int((cursor.fetchone() or {}).get("total") or 0) == 0:
                raise ValueError("该表没有 tick_time 索引。为避免全表扫描，日期覆盖统计已停止；请先为 tick_time 建立索引")

            cache_key = (str(config["database"]).lower(), safe_table.lower())
            cached_years = None
            if year is None:
                with _coverage_cache_lock:
                    cached = _coverage_year_cache.get(cache_key)
                    if cached and time() - cached[0] < COVERAGE_CACHE_TTL:
                        cached_years = [dict(item) for item in cached[1]]
            if year is None and cached_years is None:
                cursor.execute(
                    f"SELECT MIN(`tick_time`) AS min_time, MAX(`tick_time`) AS max_time FROM `{safe_table}`"
                )
                endpoints = cursor.fetchone() or {}
                if endpoints.get("min_time") is None:
                    years = []
                else:
                    first_year = endpoints["min_time"].year
                    last_year = endpoints["max_time"].year
                    years = []
                    for current_year in range(first_year, last_year + 1):
                        cursor.execute(
                            f"SELECT COUNT(*) AS total FROM `{safe_table}` WHERE `tick_time` >= %s AND `tick_time` < %s",
                            (date(current_year, 1, 1), date(current_year + 1, 1, 1)),
                        )
                        year_count = int((cursor.fetchone() or {}).get("total") or 0)
                        if year_count:
                            years.append({"year": current_year, "total": year_count})
                    years.reverse()
                with _coverage_cache_lock:
                    _coverage_year_cache[cache_key] = (time(), [dict(item) for item in years])
                    _persist_coverage_cache()
            elif year is None:
                years = cached_years

            if year is None:
                if not years:
                    return {
                        "database": config["database"], "table": selected_table,
                        "total": 0, "min_year": None, "max_year": None,
                        "periods": {"day": 0, "week": 0, "month": 0, "year": 0},
                        "years": [], "selected_year": None, "selected_year_total": 0,
                        "months": [], "selected_month": None, "selected_month_total": 0, "days": [],
                    }
                selected_year = int(years[0]["year"])
                year_total = int(years[0]["total"])
                total = sum(item["total"] for item in years)
                min_year = min(item["year"] for item in years)
                max_year = max(item["year"] for item in years)
            else:
                selected_year = int(year)
                if not 1 <= selected_year < 9999:
                    raise ValueError("所选年份超出支持范围")
                selected_year_start = date(selected_year, 1, 1)
                selected_year_end = date(selected_year + 1, 1, 1)
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM `{safe_table}` WHERE `tick_time` >= %s AND `tick_time` < %s",
                    (selected_year_start, selected_year_end),
                )
                year_total = int((cursor.fetchone() or {}).get("total") or 0)
                if year_total == 0:
                    raise ValueError("所选年份没有入库数据")
                years = None
                total = None
                min_year = None
                max_year = None
            if month is not None and not 1 <= int(month) <= 12:
                raise ValueError("月份必须在 1 到 12 之间")
            selected_month = int(month) if month is not None else None
            selected_year_start = date(selected_year, 1, 1)
            selected_year_end = date(selected_year + 1, 1, 1)
            cursor.execute(
                "SELECT MONTH(`tick_time`) AS month, COUNT(*) AS total "
                f"FROM `{safe_table}` WHERE `tick_time` >= %s AND `tick_time` < %s "
                "GROUP BY MONTH(`tick_time`) ORDER BY month",
                (selected_year_start, selected_year_end),
            )
            months = [{"month": int(row["month"]), "total": int(row["total"])} for row in cursor.fetchall()]
            if selected_month is not None:
                selected_month_start = date(selected_year, selected_month, 1)
                selected_month_end = date(selected_year + 1, 1, 1) if selected_month == 12 else date(selected_year, selected_month + 1, 1)
                cursor.execute(
                    "SELECT DAY(`tick_time`) AS day, COUNT(*) AS total "
                    f"FROM `{safe_table}` WHERE `tick_time` >= %s AND `tick_time` < %s "
                    "GROUP BY DAY(`tick_time`) ORDER BY day",
                    (selected_month_start, selected_month_end),
                )
                days = [{"day": int(row["day"]), "total": int(row["total"])} for row in cursor.fetchall()]
                month_total = next((item["total"] for item in months if item["month"] == selected_month), 0)
            else:
                days = []
                month_total = 0

            period_row = {}
            for key, start_at, end_at in (
                ("day_total", today, today + timedelta(days=1)),
                ("week_total", week_start, today + timedelta(days=1)),
                ("month_total", month_start, next_month_start),
            ):
                cursor.execute(
                    f"SELECT COUNT(*) AS total FROM `{safe_table}` WHERE `tick_time` >= %s AND `tick_time` < %s",
                    (start_at, end_at),
                )
                period_row[key] = (cursor.fetchone() or {}).get("total", 0)
            period_row["year_total"] = year_total if selected_year == today.year else 0
    finally:
        connection.close()

    return {
        "database": config["database"],
        "table": selected_table,
        "total": total,
        "min_year": min_year,
        "max_year": max_year,
        "periods": {
            "day": int(period_row.get("day_total") or 0),
            "week": int(period_row.get("week_total") or 0),
            "month": int(period_row.get("month_total") or 0),
            "year": int(period_row.get("year_total") or 0),
        },
        "years": years,
        "selected_year": selected_year,
        "selected_year_total": year_total,
        "months": months,
        "selected_month": selected_month,
        "selected_month_total": month_total,
        "days": days,
    }


def build_import_worker(file_store: FileStore, database_config_loader):
    def worker(context: TaskContext, payload: dict) -> dict:
        item = file_store.get(payload["file_id"])
        path = item["resolved_path"]
        table_name = validate_identifier(payload["table_name"], "表名")
        config = database_config_loader()
        context.log(f"正在连接数据库：{config['host']}:{config['port']} / {config['database']}")
        ensure_database(config)
        connection = connect_database(config)
        encoding = detect_encoding(path)
        total_lines = count_lines(path)
        adjuster = TimestampAdjuster(bool(payload.get("adjust_duplicates", True)))
        stats = Counter()
        file_months = Counter()
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
            context.log(f"目标数据表已确认：{table_name}")
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
                    file_months[data["tick_time"].strftime("%Y-%m")] += 1
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
                        context.progress(
                            line_num / total_lines * 98,
                            f"已处理 {line_num:,} / {total_lines:,} 行",
                        )
                    if line_num % 2000 == 0:
                        context.log(
                            f"阶段统计：已处理 {line_num:,} 行；新增 {stats['inserted']:,} 条，"
                            f"重复 {stats['duplicates']:,} 条，失败 {stats['failed_parse'] + stats['failed_insert']:,} 条"
                        )
            connection.commit()
            invalidate_coverage_cache(config["database"], table_name)
            with connection.cursor() as cursor:
                cursor.execute(
                    f"SELECT COUNT(*) AS count, MIN(tick_time) AS min_time, "
                    f"MAX(tick_time) AS max_time FROM `{table_name}`"
                )
                database_stats = cursor.fetchone()
            months = sorted(file_months)
            if months:
                month_labels = [f"{month[:4]}年{int(month[5:])}月" for month in months]
                context.log(f"数据日期范围：共涉及 {len(months)} 个月份（{'、'.join(month_labels)}）")
            else:
                month_labels = []
                context.log("未识别到有效的 Tick 日期")
            context.log(
                f"导入完成：新增 {stats['inserted']:,} 条，重复 {stats['duplicates']:,} 条，"
                f"解析失败 {stats['failed_parse']:,} 条，写入失败 {stats['failed_insert']:,} 条"
            )
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
                "file_months": months,
                "file_month_range": {"start": months[0], "end": months[-1]} if months else None,
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
