"""清洗超期专页的共享逻辑:超期检测、一刀切暂停、闸口判定与暂停记录流水。

闸口规则(写入通道与页面开关同源,以此为准):
  - 阈值空着不许开闸:threshold_hours 为空时闸口一律关闭,交单被拦回。
  - 超期自动暂停:周期运行时长超过阈值即超期,边沿触发自动扳下暂停闸。
  - 手动恢复即豁免:扫描员在超期中关掉暂停,置 overdue_override,
    同一超期不再自动扳闸;阈值变更后豁免清零,重新检测。
  - 拦住痕迹与真实拒收同批落库:写入通道拒收的瞬间,在同一事务里
    写入 intercept 流水,不存在"先拒后补"的窗口。
"""
from datetime import datetime, timezone

EVT_AUTO_PAUSE = "auto_pause"        # 超期检测自动扳闸
EVT_MANUAL_PAUSE = "manual_pause"    # 扫描员一刀切暂停
EVT_RESUME = "manual_resume"         # 扫描员关掉暂停
EVT_THRESHOLD = "threshold_set"      # 阈值设置/变更
EVT_INTERCEPT = "intercept"          # 写入通道拦回交单


def now_utc():
    return datetime.now(timezone.utc)


def lock_settings(conn):
    """FOR UPDATE 锁住单行设置,保证检测-扳闸-拦截在同一事务内串行。"""
    return conn.execute(
        "SELECT * FROM cleaning_settings WHERE id = 1 FOR UPDATE"
    ).fetchone()


def is_overdue(settings, now):
    if settings["threshold_hours"] is None or settings["cycle_started_at"] is None:
        return False
    elapsed = (now - settings["cycle_started_at"]).total_seconds() / 3600.0
    return elapsed > settings["threshold_hours"]


def gate_open(settings):
    """阈值空着不许开闸;暂停中不开闸。"""
    return settings["threshold_hours"] is not None and not settings["paused"]


def record_event(conn, event_type, actor, detail, string_code=None, at=None):
    conn.execute(
        """INSERT INTO pause_events (event_type, actor, detail, string_code, created_at)
           VALUES (%s,%s,%s,%s,%s)""",
        (event_type, actor, detail, string_code, at or now_utc()),
    )


def evaluate(conn, actor="system"):
    """超期检测:发现超期且未被豁免时,边沿触发一刀切自动暂停。

    返回 (settings, overdue)。调用方需在同一事务里提交,事件与状态同批落库。
    """
    settings = lock_settings(conn)
    overdue = is_overdue(settings, now_utc())
    if overdue and not settings["paused"] and not settings["overdue_override"]:
        settings = conn.execute(
            """UPDATE cleaning_settings
               SET paused = true, pause_source = 'auto', updated_by = %s, updated_at = %s
               WHERE id = 1
               RETURNING *""",
            (actor, now_utc()),
        ).fetchone()
        record_event(
            conn,
            EVT_AUTO_PAUSE,
            actor,
            f"清洗周期超期(上限 {settings['threshold_hours']} 小时),一刀切自动暂停整场扫描",
        )
    return settings, overdue


def status_dict(settings, overdue):
    now = now_utc()
    elapsed = None
    if settings["cycle_started_at"] is not None:
        elapsed = round((now - settings["cycle_started_at"]).total_seconds() / 3600.0, 4)
    return {
        "threshold_hours": settings["threshold_hours"],
        "cycle_started_at": settings["cycle_started_at"].isoformat()
        if settings["cycle_started_at"]
        else None,
        "elapsed_hours": elapsed,
        "overdue": overdue,
        "paused": settings["paused"],
        "pause_source": settings["pause_source"],
        "overdue_override": settings["overdue_override"],
        "gate_open": gate_open(settings),
        "updated_by": settings["updated_by"],
        "updated_at": settings["updated_at"].isoformat() if settings["updated_at"] else None,
    }
