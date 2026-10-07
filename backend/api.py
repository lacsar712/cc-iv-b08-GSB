import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect
from rules import judge
from cleaning import (
    EVT_INTERCEPT,
    EVT_MANUAL_PAUSE,
    EVT_RESUME,
    EVT_THRESHOLD,
    evaluate,
    gate_open,
    is_overdue,
    lock_settings,
    now_utc,
    record_event,
    status_dict,
)

SECRET = os.environ.get("JWT_SECRET", "pvivscan-dev-secret")
pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")
USERS = {
    "scanner": {"role": "writer", "password_hash": pwd.hash("scan123456")},
    "watcher": {"role": "reader", "password_hash": pwd.hash("watch123456")},
}


def dump(row):
    out = dict(row)
    for key, val in list(out.items()):
        if hasattr(val, "isoformat"):
            out[key] = val.isoformat()
    return out


def seed():
    with connect() as conn:
        conn.execute(SCHEMA)
        n = conn.execute("SELECT COUNT(*) AS n FROM iv_scans").fetchone()["n"]
        if n == 0:
            now = datetime.now(timezone.utc)
            samples = [
                ("阵列A-串03", 41.2, 9.1, 0.78, "合格"),
                ("阵列B-串11", 38.0, 8.4, 0.61, "衰减"),
            ]
            for code, voc, isc, ff, expect in samples:
                verdict, reason = judge(ff)
                assert verdict == expect
                conn.execute(
                    """INSERT INTO iv_scans
                       (string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                        created_by, created_at, processed_at)
                       VALUES (%s,%s,%s,%s,'done',%s,%s,'scanner',%s,%s)""",
                    (code, voc, isc, ff, verdict, reason, now, now),
                )
        conn.commit()


seed()


def user_from(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET, algorithms=["HS256"])
    except JWTError:
        return None
    sub = payload.get("sub")
    if sub not in USERS:
        return None
    return {"username": sub, "role": payload.get("role")}


def need_login(request: Request):
    user = user_from(request)
    if user is None:
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="未登录")
    return user


def need_writer(request: Request, detail: str = "仅扫描员可提交IV扫描"):
    user = need_login(request)
    if user["role"] != "writer":
        raise HTTPException(status_code=HTTP_403_FORBIDDEN, detail=detail)
    return user


@get("/api/health")
async def health() -> dict:
    return {"status": "ok", "service": "pv-string-iv-scan"}


@post("/api/auth/login")
async def login(request: Request) -> dict:
    data = await request.json()
    username = (data.get("username") or "").strip()
    password = data.get("password") or ""
    user = USERS.get(username)
    if not user or not pwd.verify(password, user["password_hash"]):
        raise HTTPException(status_code=HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    exp = datetime.now(timezone.utc) + timedelta(hours=8)
    token = jwt.encode(
        {"sub": username, "role": user["role"], "exp": exp}, SECRET, algorithm="HS256"
    )
    return {"access_token": token, "username": username, "role": user["role"]}


@get("/api/logs")
async def list_logs(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                      created_by, created_at, processed_at
               FROM iv_scans ORDER BY id DESC"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/logs", status_code=201)
async def create_log(request: Request) -> dict:
    user = need_writer(request)
    data = await request.json()
    code = (data.get("string_code") or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="组串编号不能为空")
    try:
        voc = float(data.get("voc_v"))
        isc = float(data.get("isc_a"))
        ff = float(data.get("fill_factor"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
    now = datetime.now(timezone.utc)
    rejection = None
    row = None
    with connect() as conn:
        # 超期检测直接接进写入通道:先检测(可能边沿触发自动暂停),再判闸口
        settings, _overdue = evaluate(conn, actor=user["username"])
        if not gate_open(settings):
            if settings["threshold_hours"] is None:
                rejection = "清洗阈值空着,不许开闸,交单被拦回"
            elif settings["pause_source"] == "auto":
                rejection = "清洗周期超期,已一刀切自动暂停,交单被拦回"
            else:
                rejection = "扫描已暂停,交单被拦回"
            # 拦住痕迹与真实拒收同批落库:同一事务提交,随后才返回 409
            record_event(conn, EVT_INTERCEPT, user["username"], rejection, string_code=code)
        else:
            row = conn.execute(
                """INSERT INTO iv_scans
                   (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
                   VALUES (%s,%s,%s,%s,'pending',%s,%s)
                   RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                             created_by, created_at, processed_at""",
                (code, voc, isc, ff, user["username"], now),
            ).fetchone()
    if rejection is not None:
        raise HTTPException(status_code=409, detail=rejection)
    return dump(row)


@get("/api/cleaning/status")
async def cleaning_status(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        settings, overdue = evaluate(conn)
        return status_dict(settings, overdue)


@get("/api/cleaning/events")
async def cleaning_events(request: Request) -> list:
    need_login(request)
    with connect() as conn:
        rows = conn.execute(
            """SELECT id, event_type, actor, detail, string_code, created_at
               FROM pause_events ORDER BY id DESC LIMIT 200"""
        ).fetchall()
        return [dump(r) for r in rows]


@post("/api/cleaning/threshold")
async def set_threshold(request: Request) -> dict:
    user = need_writer(request, detail="仅扫描员可设清洗阈值")
    data = await request.json()
    raw = data.get("threshold_hours")
    if raw is None or raw == "":
        value = None
    else:
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="阈值必须是数字(小时)")
        if not value > 0:
            raise HTTPException(status_code=400, detail="阈值必须大于 0")
    with connect() as conn:
        settings = lock_settings(conn)
        old = settings["threshold_hours"]
        if value is None:
            conn.execute(
                """UPDATE cleaning_settings
                   SET threshold_hours = NULL, cycle_started_at = NULL,
                       overdue_override = false, updated_by = %s, updated_at = %s
                   WHERE id = 1""",
                (user["username"], now_utc()),
            )
            record_event(conn, EVT_THRESHOLD, user["username"], "清空清洗阈值,闸口关闭(阈值空着不许开闸)")
        else:
            # 首次设置才开启新周期;后续调阈值不重置周期起点,压低上限即超期
            cycle_start = settings["cycle_started_at"]
            if old is None or cycle_start is None:
                cycle_start = now_utc()
            conn.execute(
                """UPDATE cleaning_settings
                   SET threshold_hours = %s, cycle_started_at = %s,
                       overdue_override = false, updated_by = %s, updated_at = %s
                   WHERE id = 1""",
                (value, cycle_start, user["username"], now_utc()),
            )
            record_event(
                conn, EVT_THRESHOLD, user["username"],
                f"清洗阈值 {old if old is not None else '空'} → {value} 小时",
            )
        # 阈值变更后立即检测:压低上限当场超期的,马上自动扳闸
        settings, overdue = evaluate(conn, actor=user["username"])
        return status_dict(settings, overdue)


@post("/api/cleaning/toggle")
async def toggle_pause(request: Request) -> dict:
    user = need_writer(request, detail="观察员能看不能扳,仅扫描员可扳暂停钮")
    data = await request.json()
    action = (data.get("action") or "").strip()
    if action not in ("pause", "resume"):
        raise HTTPException(status_code=400, detail="action 只能是 pause 或 resume")
    with connect() as conn:
        settings, overdue = evaluate(conn, actor=user["username"])
        if action == "pause":
            if not settings["paused"]:
                conn.execute(
                    """UPDATE cleaning_settings
                       SET paused = true, pause_source = 'manual', updated_by = %s, updated_at = %s
                       WHERE id = 1""",
                    (user["username"], now_utc()),
                )
                record_event(conn, EVT_MANUAL_PAUSE, user["username"], "扫描员一刀切暂停整场扫描")
        else:
            if settings["threshold_hours"] is None:
                raise HTTPException(status_code=400, detail="阈值空着不许开闸")
            if settings["paused"]:
                # 超期中手动恢复 = 豁免本次超期,不再自动扳闸,直到阈值变更
                conn.execute(
                    """UPDATE cleaning_settings
                       SET paused = false, pause_source = NULL, overdue_override = %s,
                           updated_by = %s, updated_at = %s
                       WHERE id = 1""",
                    (overdue, user["username"], now_utc()),
                )
                detail = "扫描员关掉暂停,恢复扫描"
                if overdue:
                    detail += "(超期中恢复,豁免本次超期)"
                record_event(conn, EVT_RESUME, user["username"], detail)
        settings = lock_settings(conn)
        return status_dict(settings, is_overdue(settings, now_utc()))


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        cleaning_status,
        cleaning_events,
        set_threshold,
        toggle_pause,
    ]
)
