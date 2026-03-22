"""
macOS Apple Calendar integration via JavaScript for Automation (osascript -l JavaScript).

Requires:
  - macOS with Calendar.app
  - Automation permission for the app running Python (Terminal, Cursor, etc.) to control Calendar

All functions return JSON strings suitable for tool results.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional


def _is_mac() -> bool:
    return sys.platform == "darwin"


def _run_jxa(source: str, payload: Dict[str, Any], timeout_s: int = 120) -> Dict[str, Any]:
    payload_s = json.dumps(payload, ensure_ascii=False)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8")
    try:
        tmp.write(source)
        tmp.flush()
        tmp_path = tmp.name
    finally:
        tmp.close()

    try:
        proc = subprocess.run(
            ["osascript", "-l", "JavaScript", tmp_path, payload_s],
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        return {"ok": False, "error": "osascript_failed", "stderr": err or out or str(proc.returncode)}
    if not out:
        return {"ok": False, "error": "empty_jxa_output", "stderr": err}
    try:
        return json.loads(out)
    except Exception:
        return {"ok": False, "error": "invalid_json_from_jxa", "raw": out[:4000], "stderr": err}


# JXA entry: function run(argv) { ... } with argv[0] = JSON payload string.

JXA_LIST_CALENDARS = r"""
function run(argv) {
  const Cal = Application('Calendar');
  const cals = Cal.calendars();
  const names = [];
  for (let i = 0; i < cals.length; i++) {
    try { names.push(String(cals[i].name())); } catch (e) {}
  }
  names.sort();
  return JSON.stringify({ ok: true, calendars: names });
}
"""

JXA_LIST_EVENTS = r"""
function run(argv) {
  const input = JSON.parse(argv[0]);
  const Cal = Application('Calendar');
  const start = new Date(input.start_iso);
  const end = new Date(input.end_iso);
  const nameFilter = input.calendar_name ? String(input.calendar_name) : '';
  const out = [];
  const cals = Cal.calendars();
  for (let i = 0; i < cals.length; i++) {
    const cal = cals[i];
    if (nameFilter && String(cal.name()) !== nameFilter) continue;
    let evs;
    try { evs = cal.events(); } catch (e) { continue; }
    for (let j = 0; j < evs.length; j++) {
      const e = evs[j];
      let sd;
      try { sd = e.startDate(); } catch (e2) { continue; }
      if (sd >= start && sd <= end) {
        let edIso = '';
        try { edIso = e.endDate().toISOString(); } catch (e3) { edIso = ''; }
        out.push({
          calendar: String(cal.name()),
          summary: String(e.summary() || ''),
          start_iso: sd.toISOString(),
          end_iso: edIso,
          allday: !!e.alldayEvent(),
          uid: String(e.uid() || ''),
          location: String(e.location() || ''),
          url: String(e.url() || ''),
        });
      }
    }
  }
  out.sort(function (a, b) { return new Date(a.start_iso) - new Date(b.start_iso); });
  return JSON.stringify({ ok: true, events: out, count: out.length });
}
"""

JXA_CREATE_EVENT = r"""
function run(argv) {
  const input = JSON.parse(argv[0]);
  const Cal = Application('Calendar');
  const calName = String(input.calendar_name || '');
  const allCals = Cal.calendars();
  let cal = null;
  for (let i = 0; i < allCals.length; i++) {
    if (String(allCals[i].name()) === calName) { cal = allCals[i]; break; }
  }
  if (!cal) {
    return JSON.stringify({ ok: false, error: 'calendar_not_found', calendar_name: calName });
  }
  const ev = Cal.Event({
    summary: String(input.title || ''),
    startDate: new Date(input.start_iso),
    endDate: new Date(input.end_iso),
    location: String(input.location || ''),
    description: String(input.notes || ''),
    alldayEvent: !!input.allday,
  });
  cal.events.push(ev);
  return JSON.stringify({ ok: true, uid: String(ev.uid()), calendar: calName, summary: String(input.title || '') });
}
"""

JXA_DELETE_EVENT = r"""
function run(argv) {
  const input = JSON.parse(argv[0]);
  const Cal = Application('Calendar');
  const calName = String(input.calendar_name || '');
  const uid = String(input.event_uid || '');
  const allCals = Cal.calendars();
  let cal = null;
  for (let i = 0; i < allCals.length; i++) {
    if (String(allCals[i].name()) === calName) { cal = allCals[i]; break; }
  }
  if (!cal) {
    return JSON.stringify({ ok: false, error: 'calendar_not_found', calendar_name: calName });
  }
  let evs;
  try { evs = cal.events(); } catch (e) {
    return JSON.stringify({ ok: false, error: 'events_unreadable' });
  }
  for (let j = 0; j < evs.length; j++) {
    if (String(evs[j].uid()) === uid) {
      evs[j].delete();
      return JSON.stringify({ ok: true, deleted_uid: uid, calendar: calName });
    }
  }
  return JSON.stringify({ ok: false, error: 'event_not_found', event_uid: uid, calendar: calName });
}
"""

JXA_UPDATE_EVENT = r"""
function run(argv) {
  const input = JSON.parse(argv[0]);
  const Cal = Application('Calendar');
  const calName = String(input.calendar_name || '');
  const uid = String(input.event_uid || '');
  const allCals = Cal.calendars();
  let cal = null;
  for (let i = 0; i < allCals.length; i++) {
    if (String(allCals[i].name()) === calName) { cal = allCals[i]; break; }
  }
  if (!cal) {
    return JSON.stringify({ ok: false, error: 'calendar_not_found', calendar_name: calName });
  }
  let evs;
  try { evs = cal.events(); } catch (e) {
    return JSON.stringify({ ok: false, error: 'events_unreadable' });
  }
  for (let j = 0; j < evs.length; j++) {
    if (String(evs[j].uid()) === uid) {
      const e = evs[j];
      if (input.title) { e.summary = String(input.title); }
      if (input.start_iso) { e.startDate = new Date(input.start_iso); }
      if (input.end_iso) { e.endDate = new Date(input.end_iso); }
      if (input.location !== undefined) { e.location = String(input.location || ''); }
      if (input.notes !== undefined) { e.description = String(input.notes || ''); }
      if (input.allday !== undefined) { e.alldayEvent = !!input.allday; }
      return JSON.stringify({ ok: true, updated_uid: uid, calendar: calName });
    }
  }
  return JSON.stringify({ ok: false, error: 'event_not_found', event_uid: uid, calendar: calName });
}
"""


def list_calendars() -> str:
    if not _is_mac():
        return json.dumps({"ok": False, "error": "apple_calendar_only_macos"})
    return json.dumps(_run_jxa(JXA_LIST_CALENDARS, {}))


def list_events(start_iso: str, end_iso: str, calendar_name: str = "") -> str:
    if not _is_mac():
        return json.dumps({"ok": False, "error": "apple_calendar_only_macos"})
    payload = {"start_iso": start_iso, "end_iso": end_iso, "calendar_name": calendar_name or ""}
    return json.dumps(_run_jxa(JXA_LIST_EVENTS, payload))


def create_event(
    calendar_name: str,
    title: str,
    start_iso: str,
    end_iso: str,
    location: str = "",
    notes: str = "",
    allday: bool = False,
) -> str:
    if not _is_mac():
        return json.dumps({"ok": False, "error": "apple_calendar_only_macos"})
    payload = {
        "calendar_name": calendar_name,
        "title": title,
        "start_iso": start_iso,
        "end_iso": end_iso,
        "location": location or "",
        "notes": notes or "",
        "allday": bool(allday),
    }
    return json.dumps(_run_jxa(JXA_CREATE_EVENT, payload))


def delete_event(calendar_name: str, event_uid: str) -> str:
    if not _is_mac():
        return json.dumps({"ok": False, "error": "apple_calendar_only_macos"})
    payload = {"calendar_name": calendar_name, "event_uid": event_uid}
    return json.dumps(_run_jxa(JXA_DELETE_EVENT, payload))


def update_event(
    calendar_name: str,
    event_uid: str,
    title: str = "",
    start_iso: str = "",
    end_iso: str = "",
    location: Optional[str] = None,
    notes: Optional[str] = None,
    allday: Optional[bool] = None,
) -> str:
    if not _is_mac():
        return json.dumps({"ok": False, "error": "apple_calendar_only_macos"})
    payload: Dict[str, Any] = {"calendar_name": calendar_name, "event_uid": event_uid}
    if title:
        payload["title"] = title
    if start_iso:
        payload["start_iso"] = start_iso
    if end_iso:
        payload["end_iso"] = end_iso
    if location is not None:
        payload["location"] = location
    if notes is not None:
        payload["notes"] = notes
    if allday is not None:
        payload["allday"] = bool(allday)
    return json.dumps(_run_jxa(JXA_UPDATE_EVENT, payload))
