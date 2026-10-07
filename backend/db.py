import os
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


def load_config(conn):
    return conn.execute(
        """SELECT id, threshold_days, paused, last_cleaned_at, updated_at, updated_by
           FROM cleaning_config WHERE id = 1"""
    ).fetchone()


def is_overdue(cfg, now=None):
    """清洗超期:已设阈值且当前时间越过 上次清洗 + 阈值天数。"""
    if cfg is None or cfg["threshold_days"] is None:
        return False
    now = now or datetime.now(timezone.utc)
    return now > cfg["last_cleaned_at"] + timedelta(days=cfg["threshold_days"])


def gate_state(cfg):
    """闸口状态:阈值空着不许开闸;暂停中闸口关闭。"""
    if cfg is None or cfg["threshold_days"] is None:
        return False, "阈值未设置,闸口保持关闭"
    if cfg["paused"]:
        return False, "扫描已暂停,闸口关闭"
    return True, "闸口开启"


SCHEMA = """
CREATE TABLE IF NOT EXISTS iv_scans (
    id serial PRIMARY KEY,
    string_code text NOT NULL,
    voc_v double precision NOT NULL,
    isc_a double precision NOT NULL,
    fill_factor double precision NOT NULL,
    status text NOT NULL DEFAULT 'pending',
    verdict text,
    reason text,
    created_by text NOT NULL,
    created_at timestamptz NOT NULL,
    processed_at timestamptz
);
CREATE TABLE IF NOT EXISTS cleaning_config (
    id integer PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    threshold_days double precision,
    paused boolean NOT NULL DEFAULT false,
    last_cleaned_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    updated_by text NOT NULL DEFAULT ''
);
INSERT INTO cleaning_config (id, threshold_days, paused, last_cleaned_at, updated_at, updated_by)
VALUES (1, NULL, false, now(), now(), 'system')
ON CONFLICT (id) DO NOTHING;
CREATE TABLE IF NOT EXISTS pause_events (
    id serial PRIMARY KEY,
    kind text NOT NULL,
    actor text NOT NULL,
    reason text NOT NULL DEFAULT '',
    string_code text,
    created_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pause_events_id ON pause_events (id DESC);
CREATE OR REPLACE FUNCTION notify_iv_scan() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify('iv_scan_new', NEW.id::text);
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_iv_scan_notify ON iv_scans;
CREATE TRIGGER trg_iv_scan_notify
AFTER INSERT ON iv_scans
FOR EACH ROW EXECUTE FUNCTION notify_iv_scan();
"""
