"""SportPulse AI: automated Slack publications via a durable activity outbox.

The employee portal stores the activity and the corresponding outbox event in
ONE transaction (SQLite for demo, PostgreSQL for real declarations). The worker
performs network delivery after that transaction has committed.

The public posting is opt-in PER ACTIVITY: no salary, home address, GPS trace,
medical data or HR decision is ever sent to Slack or to the LLM.
"""
from __future__ import annotations

import argparse
import html
import json
import logging
import os
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)
MAX_ATTEMPTS = 6
LEASE_SECONDS = 180

DEMO_SCHEMA = """
CREATE TABLE IF NOT EXISTS sportpulse_notifications_outbox (
    event_key TEXT PRIMARY KEY,
    account_id INTEGER NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
    activity_id INTEGER NOT NULL UNIQUE REFERENCES demo_activities(id) ON DELETE CASCADE,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at INTEGER NOT NULL DEFAULT 0,
    claimed_at INTEGER,
    published_at INTEGER,
    slack_channel TEXT,
    slack_ts TEXT,
    message_text TEXT,
    last_error TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sportpulse_notifications_due
ON sportpulse_notifications_outbox(status, next_attempt_at);
"""


def _mode() -> str:
    mode = os.getenv("SPORTPULSE_SLACK_MODE", "dry_run").lower()
    if mode not in {"dry_run", "live"}:
        raise ValueError("SPORTPULSE_SLACK_MODE doit être dry_run ou live")
    return mode


def _channel(sport: str) -> str:
    # Slack's channel IDs are preferred over channel names.
    try:
        mapping = json.loads(os.getenv("SPORTPULSE_SLACK_CHANNELS_JSON", "{}"))
        if not isinstance(mapping, dict):
            mapping = {}
    except (ValueError, TypeError):
        mapping = {}
    channel = mapping.get(sport) or os.getenv("SLACK_CHANNEL_DEFAULT", "")
    return str(channel).strip()


def _safe(text: str, maxlen: int = 80) -> str:
    # No Slack entity markup, mass mentions or multiline spam.
    single = " ".join(str(text).split())[:maxlen]
    return html.escape(single.replace("@", "＠"), quote=False)


def event_payload(*, display_name: str, activity_type: str, started_at: str,
                  duration_min: int, distance_km: float | None,
                  is_demo: bool) -> dict[str, Any]:
    return {
        "display_name": _safe(display_name, 80),
        "sport": str(activity_type),
        "started_at": str(started_at),
        "duration_min": int(duration_min),
        "distance_km": float(distance_km) if distance_km is not None else None,
        "is_demo": bool(is_demo),
    }


def initialize_demo_schema(conn: sqlite3.Connection) -> None:
    """Initialize outbox BEFORE entering a transaction; no implicit commit later."""
    conn.executescript(DEMO_SCHEMA)


def demo_enqueue_atomic(conn: sqlite3.Connection, *, account_id: int,
                        activity_id: int, payload: dict[str, Any]) -> str:
    """For existing portal.connect() context, schema must be prepared first."""
    key = f"demo:{activity_id}"
    conn.execute("""
        INSERT OR IGNORE INTO sportpulse_notifications_outbox
        (event_key, account_id, activity_id, payload_json, status, created_at)
        VALUES (?, ?, ?, ?, 'pending', ?)
    """, (key, account_id, activity_id, json.dumps(payload, ensure_ascii=False), int(time.time())))
    return key


def real_enqueue(cur, *, employee_id: str, activity_id: int,
                 payload: dict[str, Any]) -> str:
    """PostgreSQL INSERT in the SAME transaction as the benefits declaration."""
    key = f"real:{activity_id}"
    cur.execute("""
        INSERT INTO benefits.sportpulse_notifications_outbox
        (event_key, activity_id, employee_id, payload_json)
        VALUES (%s, %s, %s, %s::jsonb)
        ON CONFLICT (event_key) DO NOTHING
    """, (key, activity_id, employee_id, json.dumps(payload, ensure_ascii=False)))
    return key


def _motivation(payload: dict[str, Any]) -> str:
    sport = _safe(payload["sport"], 40)
    km = payload.get("distance_km")
    if km is not None:
        metrics = f"{km:.1f} km en {payload['duration_min']} min"
    else:
        metrics = f"{payload['duration_min']} min d’activité"
    fallback = f"Bravo pour cette séance de {sport} ({metrics}) ! Continue sur cette belle lancée 💪"
    if os.getenv("SPORTPULSE_MOTIVATION_BACKEND", "template") != "mistral":
        return fallback
    key = os.getenv("MISTRAL_API_KEY", "")
    if not key:
        return fallback
    # No employee name, user-written comment, account id, salary or GPS sent to Mistral.
    try:
        response = httpx.post(
            "https://api.mistral.ai/v1/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": os.getenv("SPORTPULSE_MOTIVATION_MODEL", "mistral-small-latest"),
                  "temperature": 0.6, "max_tokens": 100,
                  "messages": [
                      {"role": "system", "content": "Rédige UNE phrase chaleureuse et non compétitive en français pour encourager une activité sportive. 140 caractères maximum. N'invente aucun fait, conseil médical ou classement. Ne fournis aucune donnée personnelle."},
                      {"role": "user", "content": f"Activité: {sport}; performance: {metrics}."},
                  ]},
            timeout=8,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            return fallback
        return _safe(content, 140)
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "motivation_backend=mistral fallback=template http_status=%s",
            exc.response.status_code,
        )
        return fallback
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
        logger.warning(
            "motivation_backend=mistral fallback=template error_type=%s",
            type(exc).__name__,
        )
        return fallback


def format_message(payload: dict[str, Any]) -> str:
    icon = {"running": "🏃", "cycling": "🚴", "walking": "🚶", "hiking": "🥾",
            "swimming": "🏊", "football": "⚽", "tennis": "🎾", "yoga": "🧘"}.get(payload["sport"], "🏅")
    demo_label = " [DÉMONSTRATION — données synthétiques]" if payload.get("is_demo") else ""
    return f"{icon} *SportPulse AI*{demo_label}\n*{_safe(payload['display_name'])}* : {_motivation(payload)}"


def _post_message(channel: str, message: str) -> str:
    token = os.getenv("SLACK_BOT_TOKEN", "")
    if not token or not channel:
        raise RuntimeError("SLACK_BOT_TOKEN ou canal Slack non configuré")
    response = httpx.post("https://slack.com/api/chat.postMessage",
                          headers={"Authorization": f"Bearer {token}"},
                          json={"channel": channel, "text": message, "mrkdwn": True,
                                "unfurl_links": False, "unfurl_media": False},
                          timeout=10)
    if response.status_code == 429:
        raise RuntimeError("Slack rate_limited")
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        # Slack error codes only, never include request body or auth token.
        raise RuntimeError("Slack error: " + str(data.get("error", "unknown"))[:60])
    return str(data.get("ts", ""))


def _deliver(payload: dict[str, Any]) -> tuple[str, str, str, str]:
    channel = _channel(payload["sport"])
    message = format_message(payload)
    if _mode() == "dry_run":
        return "dry_run", channel, "", message
    ts = _post_message(channel, message)
    return "sent", channel, ts, message


def _retry_status(attempts: int) -> tuple[str, int]:
    if attempts >= MAX_ATTEMPTS:
        return "failed", 0
    return "retry", int(time.time()) + min(300, 2 ** attempts)


def process_demo(event_key: str) -> str:
    from sportpulse.employee_portal import connect
    now = int(time.time())
    with connect() as conn:
        initialize_demo_schema(conn)
        # A prior worker may have died after claiming a row. Lease expiry retries it.
        conn.execute("""
            UPDATE sportpulse_notifications_outbox
            SET status='retry', next_attempt_at=? WHERE event_key=? AND status='processing' AND claimed_at<?
        """, (now, event_key, now-LEASE_SECONDS))
        claimed = conn.execute("""
            UPDATE sportpulse_notifications_outbox
            SET status='processing', attempts=attempts+1, claimed_at=?
            WHERE event_key=? AND status IN ('pending','retry') AND next_attempt_at<=?
            RETURNING payload_json, attempts
        """, (now, event_key, now)).fetchone()
    if not claimed:
        return "not_due_or_already_done"
    payload = json.loads(claimed["payload_json"])
    try:
        status, channel, ts, message = _deliver(payload)
        with connect() as conn:
            conn.execute("""
                UPDATE sportpulse_notifications_outbox
                SET status=?, slack_channel=?, slack_ts=?, message_text=?, published_at=?, last_error=NULL
                WHERE event_key=? AND status='processing'
            """, (status, channel, ts, message, int(time.time()), event_key))
        return status
    except Exception as exc:
        new_status, retry_at = _retry_status(int(claimed["attempts"]))
        with connect() as conn:
            conn.execute("""
                UPDATE sportpulse_notifications_outbox SET status=?, next_attempt_at=?, last_error=?
                WHERE event_key=? AND status='processing'
            """, (new_status, retry_at, type(exc).__name__ + ": " + str(exc)[:90], event_key))
        logger.warning("Envoi Slack en échec pour %s: %s", event_key, type(exc).__name__)
        return new_status


def _pg_connection():
    import psycopg
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL absent")
    return psycopg.connect(dsn, connect_timeout=4)


def process_real(event_key: str) -> str:
    now = datetime.now(timezone.utc)
    with _pg_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE benefits.sportpulse_notifications_outbox
                SET status='retry', next_attempt_at=now()
                WHERE event_key=%s AND status='processing'
                  AND claimed_at < now() - interval '3 minutes'
            """, (event_key,))
            cur.execute("""
                UPDATE benefits.sportpulse_notifications_outbox
                SET status='processing', attempts=attempts+1, claimed_at=now()
                WHERE event_key=%s AND status IN ('pending','retry')
                  AND next_attempt_at<=now()
                RETURNING payload_json, attempts
            """, (event_key,))
            claimed = cur.fetchone()
    if not claimed:
        return "not_due_or_already_done"
    payload = claimed[0] if isinstance(claimed[0], dict) else json.loads(claimed[0])
    try:
        status, channel, ts, message = _deliver(payload)
        with _pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE benefits.sportpulse_notifications_outbox
                    SET status=%s, slack_channel=%s, slack_ts=%s, message_text=%s,
                        published_at=now(), last_error=NULL
                    WHERE event_key=%s AND status='processing'
                """, (status, channel, ts, message, event_key))
        return status
    except Exception as exc:
        new_status, retry_at = _retry_status(int(claimed[1]))
        retry_seconds = max(0, retry_at - int(time.time()))
        with _pg_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE benefits.sportpulse_notifications_outbox
                    SET status=%s, next_attempt_at=now() + (%s * interval '1 second'), last_error=%s
                    WHERE event_key=%s AND status='processing'
                """, (new_status, retry_seconds, type(exc).__name__ + ": " + str(exc)[:90], event_key))
        logger.warning("Envoi Slack en échec pour %s: %s", event_key, type(exc).__name__)
        return new_status


def process_event(backend: str, event_key: str) -> str:
    """FastAPI BackgroundTask entry point; never propagate exceptions to the client."""
    try:
        return process_demo(event_key) if backend == "demo" else process_real(event_key)
    except Exception as exc:
        logger.exception("Erreur worker outbox (%s/%s): %s", backend, event_key, type(exc).__name__)
        return "worker_error"


def process_due(max_events: int = 20) -> dict[str, int]:
    """Independent worker retries and restarts; never republishes 'sent'/'dry_run'."""
    counts: dict[str, int] = {}
    from sportpulse.employee_portal import connect
    with connect() as conn:
        initialize_demo_schema(conn)
        now = int(time.time())
        conn.execute("""UPDATE sportpulse_notifications_outbox SET status='retry', next_attempt_at=?
                       WHERE status='processing' AND claimed_at<?""", (now, now-LEASE_SECONDS))
        keys = [r[0] for r in conn.execute("""
            SELECT event_key FROM sportpulse_notifications_outbox
            WHERE status IN ('pending','retry') AND next_attempt_at<=?
            ORDER BY created_at ASC LIMIT ?
        """, (now, max_events)).fetchall()]
    for key in keys:
        status = process_event("demo", key)
        counts[status] = counts.get(status, 0) + 1
    if os.getenv("DATABASE_URL"):
        try:
            with _pg_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE benefits.sportpulse_notifications_outbox
                        SET status='retry', next_attempt_at=now()
                        WHERE status='processing' AND claimed_at<now()-interval '3 minutes'
                    """)
                    cur.execute("""
                        SELECT event_key FROM benefits.sportpulse_notifications_outbox
                        WHERE status IN ('pending','retry') AND next_attempt_at<=now()
                        ORDER BY created_at LIMIT %s
                    """, (max_events,))
                    real_keys = [r[0] for r in cur.fetchall()]
            for key in real_keys:
                status = process_event("real", key)
                counts[status] = counts.get(status, 0) + 1
        except Exception as exc:
            logger.warning("Outbox PostgreSQL non accessible: %s", type(exc).__name__)
    # Étape 7 : republier les corrections/suppressions sans refaire les posts initiaux.
    from sportpulse.activity_management import process_corrections
    for k, v in process_corrections().items():
        counts['activity_correction_' + k] = v
    return counts


def demo_notification_rows(account_id: int, limit: int = 20) -> list[dict[str, Any]]:
    from sportpulse.employee_portal import connect
    with connect() as conn:
        initialize_demo_schema(conn)
        rows = conn.execute("""
            SELECT event_key, status, created_at, published_at, slack_channel, message_text
            FROM sportpulse_notifications_outbox
            WHERE account_id=? ORDER BY created_at DESC, rowid DESC LIMIT ?
        """, (account_id, limit)).fetchall()
    return [dict(r) for r in rows]


def main() -> None:
    p = argparse.ArgumentParser(description="Worker Slack SportPulse")
    sub = p.add_subparsers(dest="command", required=True)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    worker.add_argument("--interval", type=int, default=5)
    args = p.parse_args()
    if args.command == "worker":
        logging.basicConfig(level=logging.INFO)
        while True:
            print(json.dumps(process_due(), ensure_ascii=False), flush=True)
            if args.once:
                break
            time.sleep(max(2, args.interval))


if __name__ == "__main__":
    main()
