#!/usr/bin/env python3
"""
BBMP / GBA Service Status Monitor — main entry point.

Run manually:
    python scripts/monitor.py --config config/services.yaml --data-dir docs/data

Flags:
    --dry-run     Do everything except actually send notifications (prints
                  what WOULD be sent instead). Useful for testing.
    --once        (default behaviour) run a single check pass, as designed
                  to be invoked by the GitHub Actions cron schedule.

This script is intentionally dependency-light (requests + PyYAML) so it runs
comfortably inside a free GitHub Actions runner in a few seconds.
"""
from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

import yaml

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from checker import check_service, CheckResult  # noqa: E402
from notify import notify_all  # noqa: E402
from utils import (  # noqa: E402
    utc_now, iso, parse_iso, load_json, save_json,
    append_jsonl, read_jsonl, write_jsonl, fmt_duration,
)

STATUS_OPERATIONAL = "OPERATIONAL"
STATUS_DEGRADED = "DEGRADED"
STATUS_DOWN = "DOWN"
STATUS_UNKNOWN = "UNKNOWN"


# --------------------------------------------------------------------------- 
# Config loading
# --------------------------------------------------------------------------- 

def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    if "settings" not in cfg or "services" not in cfg:
        raise ValueError("config file must contain 'settings' and 'services' keys")
    return cfg


# --------------------------------------------------------------------------- 
# Per-service health classification
# --------------------------------------------------------------------------- 

def classify_health(result: CheckResult) -> str:
    """Return 'healthy' | 'degraded' | 'down' for a single check result."""
    if not result.ok:
        return "down"
    if result.keyword_ok is False:
        return "degraded"
    return "healthy"


def default_service_state() -> dict:
    return {
        "status": STATUS_UNKNOWN,
        "consecutive_success": 0,
        "consecutive_fail": 0,
        "consecutive_keyword_fail": 0,
        "last_check": None,
        "last_success": None,
        "last_failure": None,
        "last_http_status": None,
        "last_response_time_ms": None,
        "last_error_type": "",
        "last_error_message": "",
        "current_outage_start": None,
        "last_notified_down_at": None,
    }


def apply_transition(
    svc_id: str,
    svc_name: str,
    state: dict,
    result: CheckResult,
    settings: dict,
    now_iso: str,
) -> Optional[dict]:
    """
    Mutates `state` in place based on the latest check result and the
    configured thresholds. Returns a transition event dict if a
    notification-worthy transition happened this run, else None.
    """
    health = classify_health(result)
    fail_threshold = settings["fail_threshold"]
    success_threshold = settings["success_threshold"]
    degraded_threshold = settings["degraded_threshold"]

    state["last_check"] = now_iso
    state["last_http_status"] = result.http_status
    state["last_response_time_ms"] = result.response_time_ms
    state["last_error_type"] = result.error_type
    state["last_error_message"] = result.error_message

    transition = None
    prev_status = state["status"]

    if health == "healthy":
        state["consecutive_success"] += 1
        state["consecutive_fail"] = 0
        state["consecutive_keyword_fail"] = 0
        state["last_success"] = now_iso

        if prev_status in (STATUS_DOWN, STATUS_DEGRADED, STATUS_UNKNOWN):
            if state["consecutive_success"] >= success_threshold or prev_status == STATUS_UNKNOWN:
                if prev_status == STATUS_DOWN:
                    outage_start = state.get("current_outage_start")
                    duration = None
                    if outage_start:
                        duration = (parse_iso(now_iso) - parse_iso(outage_start)).total_seconds()
                    transition = {
                        "type": "RECOVERED",
                        "service_id": svc_id,
                        "service_name": svc_name,
                        "outage_start": outage_start,
                        "recovered_at": now_iso,
                        "downtime_seconds": duration,
                        "reason": None,
                    }
                elif prev_status == STATUS_DEGRADED:
                    transition = {
                        "type": "RECOVERED_FROM_DEGRADED",
                        "service_id": svc_id,
                        "service_name": svc_name,
                        "recovered_at": now_iso,
                    }
                state["status"] = STATUS_OPERATIONAL
                state["current_outage_start"] = None
                state["last_notified_down_at"] = None
        # else already OPERATIONAL: nothing to do

    elif health == "degraded":
        state["last_failure"] = now_iso
        state["consecutive_fail"] = 0
        state["consecutive_success"] = 0
        state["consecutive_keyword_fail"] += 1

        if prev_status != STATUS_DOWN:
            if state["consecutive_keyword_fail"] >= degraded_threshold and prev_status != STATUS_DEGRADED:
                transition = {
                    "type": "DEGRADED",
                    "service_id": svc_id,
                    "service_name": svc_name,
                    "started_at": now_iso,
                    "reason": "Website reachable but expected content not found "
                              "(application may be partially broken).",
                }
                state["status"] = STATUS_DEGRADED
            elif prev_status == STATUS_UNKNOWN and state["consecutive_keyword_fail"] < degraded_threshold:
                pass  # stay UNKNOWN until confirmed

    else:  # down
        state["last_failure"] = now_iso
        state["consecutive_success"] = 0
        state["consecutive_keyword_fail"] = 0
        state["consecutive_fail"] += 1

        if prev_status != STATUS_DOWN:
            if state["consecutive_fail"] >= fail_threshold:
                state["current_outage_start"] = state["current_outage_start"] or now_iso
                transition = {
                    "type": "DOWN",
                    "service_id": svc_id,
                    "service_name": svc_name,
                    "started_at": state["current_outage_start"],
                    "reason": f"{result.error_type}: {result.error_message}".strip(": "),
                }
                state["status"] = STATUS_DOWN
                state["last_notified_down_at"] = now_iso
            # else: stays in prior status (UNKNOWN/OPERATIONAL/DEGRADED) until threshold met
        else:
            # already down - possible repeat reminder, handled by caller
            pass

    return transition


def maybe_repeat_reminder(state: dict, settings: dict, now_iso: str) -> bool:
    """Return True if a 'still down' reminder should be sent now."""
    repeat_minutes = settings.get("repeat_notify_minutes", 0)
    if repeat_minutes <= 0:
        return False
    if state["status"] != STATUS_DOWN:
        return False
    last_notified = state.get("last_notified_down_at")
    if not last_notified:
        return True
    elapsed_min = (parse_iso(now_iso) - parse_iso(last_notified)).total_seconds() / 60.0
    return elapsed_min >= repeat_minutes


# --------------------------------------------------------------------------- 
# Incidents
# --------------------------------------------------------------------------- 

def record_incident_open(incidents: list, svc_id: str, svc_name: str, started_at: str, reason: str) -> None:
    incidents.insert(0, {
        "service_id": svc_id,
        "service_name": svc_name,
        "started_at": started_at,
        "resolved_at": None,
        "duration_seconds": None,
        "reason": reason,
    })


def record_incident_close(incidents: list, svc_id: str, resolved_at: str) -> None:
    for inc in incidents:
        if inc["service_id"] == svc_id and inc["resolved_at"] is None:
            inc["resolved_at"] = resolved_at
            inc["duration_seconds"] = (parse_iso(resolved_at) - parse_iso(inc["started_at"])).total_seconds()
            return


# --------------------------------------------------------------------------- 
# History / uptime
# --------------------------------------------------------------------------- 

def prune_history(history: list[dict], retention_days: int, now) -> list[dict]:
    cutoff = now.timestamp() - retention_days * 86400
    return [h for h in history if parse_iso(h["ts"]).timestamp() >= cutoff]


def uptime_pct(history: list[dict], svc_id: str, now, window_hours: float) -> Optional[float]:
    cutoff = now.timestamp() - window_hours * 3600
    relevant = [h for h in history if h["service_id"] == svc_id and parse_iso(h["ts"]).timestamp() >= cutoff]
    if not relevant:
        return None
    healthy = sum(1 for h in relevant if h["health"] == "healthy")
    return round(100.0 * healthy / len(relevant), 2)


def build_timeline(history: list[dict], svc_id: str, now, buckets: int = 24, bucket_hours: float = 1.0) -> list[str]:
    """
    Returns a list of `buckets` entries (oldest first), one per bucket_hours
    window, each one of UP / DOWN / DEGRADED / UNKNOWN, summarising the worst
    health seen for that service in that window.
    """
    now_ts = now.timestamp()
    result = []
    relevant = [h for h in history if h["service_id"] == svc_id]
    for i in range(buckets, 0, -1):
        bucket_end = now_ts - (i - 1) * bucket_hours * 3600
        bucket_start = bucket_end - bucket_hours * 3600
        in_bucket = [h for h in relevant if bucket_start <= parse_iso(h["ts"]).timestamp() < bucket_end]
        if not in_bucket:
            result.append("UNKNOWN")
            continue
        healths = {h["health"] for h in in_bucket}
        if "down" in healths:
            result.append("DOWN")
        elif "degraded" in healths:
            result.append("DEGRADED")
        else:
            result.append("UP")
    return result


# --------------------------------------------------------------------------- 
# Notification message building
# --------------------------------------------------------------------------- 

def format_transition_message(t: dict) -> str:
    if t["type"] == "DOWN":
        return (
            f"\U0001F534 <b>{t['service_name']}</b> is DOWN\n"
            f"Started: {t['started_at']}\n"
            f"Reason: {t['reason'] or 'unknown'}"
        )
    if t["type"] == "RECOVERED":
        dt = t.get("downtime_seconds")
        downtime_str = fmt_duration(dt) if dt is not None else "unknown"
        return (
            f"\U0001F7E2 <b>{t['service_name']}</b> is BACK ONLINE\n"
            f"Outage start: {t.get('outage_start') or 'unknown'}\n"
            f"Recovery time: {t['recovered_at']}\n"
            f"Approx downtime: {downtime_str}"
        )
    if t["type"] == "DEGRADED":
        return (
            f"\U0001F7E1 <b>{t['service_name']}</b> is DEGRADED\n"
            f"Since: {t['started_at']}\n"
            f"Reason: {t['reason']}"
        )
    if t["type"] == "RECOVERED_FROM_DEGRADED":
        return f"\U0001F7E2 <b>{t['service_name']}</b> content check passed again — no longer degraded."
    return f"{t}"


def format_repeat_reminder(svc_name: str, state: dict, now_iso: str) -> str:
    outage_start = state.get("current_outage_start")
    downtime = None
    if outage_start:
        downtime = (parse_iso(now_iso) - parse_iso(outage_start)).total_seconds()
    downtime_str = fmt_duration(downtime) if downtime is not None else "unknown"
    return (
        f"\U0001F534 <b>{svc_name}</b> is STILL DOWN\n"
        f"Outage started: {outage_start or 'unknown'}\n"
        f"Downtime so far: {downtime_str}\n"
        f"Last error: {state.get('last_error_type')}: {state.get('last_error_message')}"
    )


# --------------------------------------------------------------------------- 
# Main
# --------------------------------------------------------------------------- 

def run(config_path: str, data_dir: str, dry_run: bool = False) -> dict:
    cfg = load_config(config_path)
    settings = cfg["settings"]
    services_cfg = [s for s in cfg["services"] if s.get("enabled", True)]

    state_path = os.path.join(data_dir, "state.json")
    history_path = os.path.join(data_dir, "history.jsonl")
    incidents_path = os.path.join(data_dir, "incidents.json")
    status_path = os.path.join(data_dir, "status.json")

    state_all = load_json(state_path, {})
    incidents = load_json(incidents_path, [])
    history = read_jsonl(history_path)

    now = utc_now()
    now_iso = iso(now)

    transitions = []
    reminders = []

    for svc in services_cfg:
        svc_id = svc["id"]
        state = state_all.get(svc_id) or default_service_state()

        result = check_service(
            url=svc["url"],
            expect_status=svc.get("expect_status", [200]),
            keyword=svc.get("keyword"),
            timeout_seconds=settings["timeout_seconds"],
            user_agent=settings["user_agent"],
        )
        health = classify_health(result)

        transition = apply_transition(svc_id, svc["name"], state, result, settings, now_iso)

        # history record (one line per check, per service, per run)
        append_jsonl(history_path, {
            "ts": now_iso,
            "service_id": svc_id,
            "health": health,
            "status_after": state["status"],
            "http_status": result.http_status,
            "response_time_ms": result.response_time_ms,
            "error_type": result.error_type,
        })

        if transition:
            transitions.append(transition)
            if transition["type"] == "DOWN":
                record_incident_open(incidents, svc_id, svc["name"], transition["started_at"], transition["reason"])
            elif transition["type"] == "RECOVERED":
                record_incident_close(incidents, svc_id, transition["recovered_at"])
        elif maybe_repeat_reminder(state, settings, now_iso):
            reminders.append((svc["name"], dict(state)))
            state["last_notified_down_at"] = now_iso

        state_all[svc_id] = state

    # persist state + incidents immediately (independent of notification success)
    save_json(state_path, state_all)
    save_json(incidents_path, incidents[:500])

    # reload+prune history (append_jsonl above only appended; prune now)
    history = read_jsonl(history_path)
    history = prune_history(history, settings["history_retention_days"], now)
    write_jsonl(history_path, history)

    # ---- build dashboard status.json ----
    service_payloads = []
    down_count = 0
    degraded_count = 0
    for svc in services_cfg:
        svc_id = svc["id"]
        state = state_all[svc_id]
        outage_duration = None
        if state["status"] == STATUS_DOWN and state.get("current_outage_start"):
            outage_duration = (now - parse_iso(state["current_outage_start"])).total_seconds()

        if state["status"] == STATUS_DOWN:
            down_count += 1
        elif state["status"] == STATUS_DEGRADED:
            degraded_count += 1

        service_payloads.append({
            "id": svc_id,
            "name": svc["name"],
            "category": svc.get("category", "Service"),
            "url": svc["url"],
            "check_type": svc.get("check_type"),
            "verified_note": svc.get("verified_note", ""),
            "status": state["status"],
            "last_checked": state["last_check"],
            "last_success": state["last_success"],
            "last_failure": state["last_failure"],
            "response_time_ms": state["last_response_time_ms"],
            "http_status": state["last_http_status"],
            "last_error_type": state["last_error_type"],
            "outage_duration_seconds": outage_duration,
            "uptime_24h": uptime_pct(history, svc_id, now, 24),
            "uptime_7d": uptime_pct(history, svc_id, now, 24 * 7),
            "uptime_30d": uptime_pct(history, svc_id, now, 24 * 30),
            "timeline_24h": build_timeline(history, svc_id, now, buckets=24, bucket_hours=1.0),
        })

    total = len(services_cfg)
    unknown_count = sum(1 for s in service_payloads if s["status"] == STATUS_UNKNOWN)

    if total == 0:
        overall = STATUS_UNKNOWN
    elif down_count >= max(1, total // 2) and down_count > 0:
        overall = "MAJOR_OUTAGE"
    elif down_count > 0 or degraded_count > 0:
        overall = "PARTIAL_OUTAGE"
    elif unknown_count == total:
        # No service has been confirmed healthy yet (e.g. very first run) -
        # never claim "all operational" without at least one confirmed check.
        overall = "CHECKING"
    elif unknown_count > 0:
        overall = "PARTIAL_OUTAGE"
    else:
        overall = "ALL_OPERATIONAL"

    status_payload = {
        "generated_at": now_iso,
        "overall": overall,
        "services": service_payloads,
        "incidents": incidents[:20],
        "settings": {
            "fail_threshold": settings["fail_threshold"],
            "success_threshold": settings["success_threshold"],
            "degraded_threshold": settings["degraded_threshold"],
            "check_interval_note": "~every 5 minutes via GitHub Actions (best-effort)",
        },
    }
    save_json(status_path, status_payload)

    # ---- notifications ----
    if transitions:
        if len(transitions) == 1:
            notify_all(
                subject=f"BBMP/GBA status change: {transitions[0]['service_name']}",
                message=format_transition_message(transitions[0]),
                dry_run=dry_run,
            )
        else:
            combined = "\n\n".join(format_transition_message(t) for t in transitions)
            notify_all(
                subject=f"BBMP/GBA status changes ({len(transitions)})",
                message=combined,
                dry_run=dry_run,
            )

    for svc_name, state in reminders:
        notify_all(
            subject=f"BBMP/GBA still down: {svc_name}",
            message=format_repeat_reminder(svc_name, state, now_iso),
            dry_run=dry_run,
        )

    save_json(state_path, state_all)  # persist any reminder timestamp updates

    return status_payload


def main():
    parser = argparse.ArgumentParser(description="BBMP/GBA Service Status Monitor")
    parser.add_argument("--config", default="config/services.yaml")
    parser.add_argument("--data-dir", default="docs/data")
    parser.add_argument("--dry-run", action="store_true", help="Do not actually send notifications")
    args = parser.parse_args()

    payload = run(args.config, args.data_dir, dry_run=args.dry_run)
    down = [s["name"] for s in payload["services"] if s["status"] == STATUS_DOWN]
    degraded = [s["name"] for s in payload["services"] if s["status"] == STATUS_DEGRADED]
    unknown = [s["name"] for s in payload["services"] if s["status"] == STATUS_UNKNOWN]
    print(f"Overall: {payload['overall']}")
    if down:
        print(f"DOWN: {', '.join(down)}")
    if degraded:
        print(f"DEGRADED: {', '.join(degraded)}")
    if unknown:
        print(f"UNKNOWN (not yet confirmed): {', '.join(unknown)}")
    if not down and not degraded and not unknown:
        print("All monitored services operational.")


if __name__ == "__main__":
    main()
