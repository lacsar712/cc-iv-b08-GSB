import os
import psycopg
from psycopg.rows import dict_row

DSN = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:54402/pvivscan")


def connect():
    return psycopg.connect(DSN, row_factory=dict_row)


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

-- 清洗超期专页:单行设置表,threshold_hours 为空表示阈值未设置(不许开闸)
CREATE TABLE IF NOT EXISTS cleaning_settings (
    id smallint PRIMARY KEY DEFAULT 1 CHECK (id = 1),
    threshold_hours double precision,
    cycle_started_at timestamptz,
    paused boolean NOT NULL DEFAULT false,
    pause_source text,
    overdue_override boolean NOT NULL DEFAULT false,
    updated_by text,
    updated_at timestamptz
);
INSERT INTO cleaning_settings (id) VALUES (1) ON CONFLICT (id) DO NOTHING;

-- 暂停记录流水:暂停/恢复/阈值变更/拦截痕迹都落这张表
CREATE TABLE IF NOT EXISTS pause_events (
    id serial PRIMARY KEY,
    event_type text NOT NULL,
    actor text NOT NULL,
    detail text NOT NULL,
    string_code text,
    created_at timestamptz NOT NULL
);
"""
