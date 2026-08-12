"""fold — 이벤트 로그 → materialized view (설계서 §2-4).

| 이벤트      | 뷰 반영 |
| push       | sections 교체 + know 이월 보존(F1 (a)), title 갱신, updated/last_* 갱신 |
| decide     | decisions[]에 append (수정·삭제 없음 — N1) |
| checkpoint | updated 갱신만 |
| close      | status = outcome |
"""

from __future__ import annotations

from ..core.util import carryover_lines, kst_date, normalize_ws, sensitivity_max


def fold_events(events: list[dict]) -> dict | None:
    """복호화된 이벤트 목록 → view 본문. 이벤트가 없으면 None."""
    if not events:
        return None
    # ts는 초 단위(§1) — 같은 초의 순서는 ord(허브가 append 시 기록한 epoch ns)로 구분.
    # (/hk:close가 push 직후 close를 보내는 경로에서 필수)
    events = sorted(events, key=lambda e: (e.get("ts", ""), e.get("ord", 0), e.get("_id", "")))
    view: dict | None = None
    for e in events:
        if view is None:
            view = {
                "thread": e["thread"],
                "project_id": e.get("project_id", ""),
                "title": "",
                "status": "active",
                "sensitivity": e.get("sensitivity") or "tech",
                "created": e.get("ts", ""),
                "updated": e.get("ts", ""),
                "last_tool": e.get("tool", ""),
                "last_device": e.get("device", ""),
                "events": 0,
                "sections": {},
                "know_carryover": [],
                "decisions": [],
                "tags": [],
            }
        view["events"] += 1
        view["updated"] = e.get("ts", view["updated"])
        etype = e.get("type")
        if etype == "push":
            new_sections = dict(e.get("sections") or {})
            new_know = new_sections.get("know", "")
            new_know_set = {normalize_ws(ln) for ln in carryover_lines(new_know)}
            prev_lines = carryover_lines(view["sections"].get("know", "")) + list(
                view["know_carryover"]
            )
            carry: list[str] = []
            seen: set[str] = set()
            for line in prev_lines:
                norm = normalize_ws(line)
                if norm and norm not in new_know_set and norm not in seen:
                    carry.append(line)
                    seen.add(norm)
            view["know_carryover"] = carry
            view["sections"] = new_sections
            view["title"] = e.get("title") or view["title"]
            view["sensitivity"] = sensitivity_max(view["sensitivity"], e.get("sensitivity"))
            view["status"] = "active"  # reopen:true push 포함 — push는 스레드를 활성으로 되돌린다
            view["last_tool"] = e.get("tool", view["last_tool"])
            view["last_device"] = e.get("device", view["last_device"])
        elif etype == "decide":
            d = dict(e.get("decision") or {})
            d.setdefault("date", kst_date(e.get("ts")))
            d.setdefault("tool", e.get("tool", ""))
            view["decisions"].append(d)
        elif etype == "checkpoint":
            pass  # updated 갱신만 (§2-4)
        elif etype == "close":
            view["status"] = e.get("outcome", "done")
    return view
