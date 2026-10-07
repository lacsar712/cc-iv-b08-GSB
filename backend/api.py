import os
from datetime import datetime, timedelta, timezone
from functools import wraps

from jose import JWTError, jwt
from litestar import Litestar, Request, get, post, put
from litestar.exceptions import HTTPException
from litestar.response import Response
from litestar.status_codes import HTTP_401_UNAUTHORIZED, HTTP_403_FORBIDDEN
from passlib.context import CryptContext

from db import SCHEMA, connect, gate_state, is_overdue, load_config
from rules import judge

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


def fetch_cleaning_state(conn) -> dict:
    cfg = load_config(conn)
    events = conn.execute(
        """SELECT id, kind, actor, reason, string_code, created_at
           FROM pause_events ORDER BY id DESC LIMIT 100"""
    ).fetchall()
    open_, reason = gate_state(cfg)
    return {
        "threshold_days": cfg["threshold_days"],
        "paused": cfg["paused"],
        "last_cleaned_at": cfg["last_cleaned_at"].isoformat(),
        "updated_at": cfg["updated_at"].isoformat(),
        "updated_by": cfg["updated_by"],
        "overdue": is_overdue(cfg),
        "gate_open": open_,
        "gate_reason": reason,
        "events": [dump(e) for e in events],
    }


@get("/api/cleaning")
async def cleaning_state(request: Request) -> dict:
    need_login(request)
    with connect() as conn:
        return fetch_cleaning_state(conn)


@put("/api/cleaning/threshold")
async def set_threshold(request: Request) -> dict:
    user = need_writer(request, "仅扫描员可改清洗阈值")
    data = await request.json()
    raw = data.get("threshold_days")
    if raw is None or raw == "":
        threshold = None
    else:
        try:
            threshold = float(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="阈值必须是数字或留空")
        if threshold < 0:
            raise HTTPException(status_code=400, detail="阈值不能为负数")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        cfg = conn.execute(
            "SELECT threshold_days, paused, last_cleaned_at FROM cleaning_config WHERE id = 1 FOR UPDATE"
        ).fetchone()
        conn.execute(
            "UPDATE cleaning_config SET threshold_days=%s, updated_at=%s, updated_by=%s WHERE id=1",
            (threshold, now, user["username"]),
        )
        cfg["threshold_days"] = threshold
        if threshold is not None and not cfg["paused"] and is_overdue(cfg, now):
            conn.execute("UPDATE cleaning_config SET paused=true WHERE id=1")
            conn.execute(
                """INSERT INTO pause_events (kind, actor, reason, created_at)
                   VALUES ('pause', 'system', %s, %s)""",
                (f"清洗周期超期(阈值 {threshold} 天),超期检测自动一刀切暂停", now),
            )
        state = fetch_cleaning_state(conn)
        conn.commit()
        return state


@post("/api/cleaning/pause")
async def pause_scan(request: Request) -> dict:
    user = need_writer(request, "仅扫描员可扳暂停钮")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        cfg = conn.execute(
            "SELECT paused FROM cleaning_config WHERE id = 1 FOR UPDATE"
        ).fetchone()
        if not cfg["paused"]:
            conn.execute(
                "UPDATE cleaning_config SET paused=true, updated_at=%s, updated_by=%s WHERE id=1",
                (now, user["username"]),
            )
            conn.execute(
                """INSERT INTO pause_events (kind, actor, reason, created_at)
                   VALUES ('pause', %s, %s, %s)""",
                (user["username"], "手动一刀切暂停整场扫描", now),
            )
        state = fetch_cleaning_state(conn)
        conn.commit()
        return state


@post("/api/cleaning/resume")
async def resume_scan(request: Request) -> dict:
    user = need_writer(request, "仅扫描员可扳暂停钮")
    now = datetime.now(timezone.utc)
    with connect() as conn:
        cfg = conn.execute(
            "SELECT threshold_days, paused FROM cleaning_config WHERE id = 1 FOR UPDATE"
        ).fetchone()
        if cfg["threshold_days"] is None:
            raise HTTPException(status_code=400, detail="阈值空着不许开闸")
        if cfg["paused"]:
            conn.execute(
                "UPDATE cleaning_config SET paused=false, updated_at=%s, updated_by=%s WHERE id=1",
                (now, user["username"]),
            )
            conn.execute(
                """INSERT INTO pause_events (kind, actor, reason, created_at)
                   VALUES ('resume', %s, %s, %s)""",
                (user["username"], "手动开闸恢复扫描", now),
            )
        state = fetch_cleaning_state(conn)
        conn.commit()
        return state


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
    now = datetime.now(timezone.utc)
    with connect() as conn:
        open_, reason = gate_state(load_config(conn))
        if not open_:
            # 拦住痕迹必须与真实拒收同批落库:同一事务写入拒收流水后再回 409。
            conn.execute(
                """INSERT INTO pause_events (kind, actor, reason, string_code, created_at)
                   VALUES ('reject', %s, %s, %s, %s)""",
                (user["username"], reason, code or None, now),
            )
            conn.commit()
            raise HTTPException(status_code=409, detail=f"{reason},本次交单已拒收并留痕")
        if not code:
            raise HTTPException(status_code=400, detail="组串编号不能为空")
        try:
            voc = float(data.get("voc_v"))
            isc = float(data.get("isc_a"))
            ff = float(data.get("fill_factor"))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="电压电流与填充因子必须是数字")
        row = conn.execute(
            """INSERT INTO iv_scans
               (string_code, voc_v, isc_a, fill_factor, status, created_by, created_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s)
               RETURNING id, string_code, voc_v, isc_a, fill_factor, status, verdict, reason,
                         created_by, created_at, processed_at""",
            (code, voc, isc, ff, user["username"], now),
        ).fetchone()
        conn.commit()
        return dump(row)


app = Litestar(
    route_handlers=[
        health,
        login,
        list_logs,
        create_log,
        cleaning_state,
        set_threshold,
        pause_scan,
        resume_scan,
    ]
)
