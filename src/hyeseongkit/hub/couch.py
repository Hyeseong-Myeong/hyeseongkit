"""CouchDB 비동기 클라이언트 — httpx로 HTTP API 직접 호출 (설계서 §0-4, 제약 L1).

폴링 금지(L3): 변경 감지는 `_changes` continuous 피드 구독으로만 한다.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import quote

import httpx


class CouchDBDown(Exception):
    """CouchDB 접속 불가 — 라우트에서 503 COUCHDB_DOWN으로 변환 (§3-1)."""


class CouchConflict(Exception):
    """문서 갱신 충돌 (409)."""


class CouchClient:
    def __init__(self, base_url: str, user: str = "", password: str = ""):
        auth = (user, password) if user else None
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=httpx.Timeout(10.0, read=70.0),
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            r = await self._client.get("/_up")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def ensure_db(self, db: str) -> None:
        """DB가 없으면 생성. 이미 있으면 통과.

        HK_COUCHDB_USER는 서버 관리자가 아니라 hk_hub이므로(F4) DB 생성 권한이 없다 —
        3개 DB는 배포 절차(§12-3)에서 관리자가 미리 만든다. 여기서는 존재만 확인하고,
        없는데 만들 수도 없으면 무엇을 해야 하는지 알려주고 기동을 멈춘다.
        """
        try:
            head = await self._client.head(f"/{db}")
            if head.status_code == 200:
                return
            r = await self._client.put(f"/{db}")
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
        if r.status_code in (201, 202, 412):
            return
        if r.status_code in (401, 403):
            raise CouchDBDown(
                f"DB '{db}'가 없고 생성 권한도 없습니다 — 서버 관리자로 DB를 만들고 "
                f"HK_COUCHDB_USER를 해당 DB의 _security.admins에 등록하세요 (설계서 §12-3)"
            )
        raise CouchDBDown(f"DB 생성 실패 {db}: {r.status_code} {r.text[:200]}")

    async def get(self, db: str, doc_id: str) -> dict | None:
        try:
            r = await self._client.get(f"/{db}/{quote(doc_id, safe='')}")
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise CouchDBDown(f"GET {doc_id}: {r.status_code}")
        return r.json()

    async def put(self, db: str, doc: dict) -> str:
        try:
            r = await self._client.put(f"/{db}/{quote(doc['_id'], safe='')}", json=doc)
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
        if r.status_code in (201, 202):
            return r.json().get("rev", "")
        if r.status_code == 409:
            raise CouchConflict(doc["_id"])
        raise CouchDBDown(f"PUT {doc['_id']}: {r.status_code} {r.text[:200]}")

    async def find(
        self,
        db: str,
        selector: dict,
        *,
        limit: int = 100,
        fields: list[str] | None = None,
    ) -> list[dict]:
        body: dict = {"selector": selector, "limit": limit}
        if fields:
            body["fields"] = fields
        try:
            r = await self._client.post(f"/{db}/_find", json=body)
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
        if r.status_code != 200:
            raise CouchDBDown(f"_find: {r.status_code} {r.text[:200]}")
        return r.json().get("docs", [])

    async def ensure_index(self, db: str, fields: list[str], name: str) -> None:
        body = {"index": {"fields": fields}, "name": name, "type": "json"}
        try:
            r = await self._client.post(f"/{db}/_index", json=body)
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
        if r.status_code in (200, 201):
            return
        if r.status_code in (401, 403):
            # 인덱스는 설계 문서라 DB 관리자 권한이 필요하다 (멤버 권한으로는 불가)
            raise CouchDBDown(
                f"인덱스 '{name}' 생성 권한이 없습니다 — HK_COUCHDB_USER를 "
                f"'{db}'의 _security.admins에 등록하세요 (설계서 §12-3)"
            )
        raise CouchDBDown(f"_index {name}: {r.status_code} {r.text[:200]}")

    async def all_docs_prefix(self, db: str, prefix: str) -> list[dict]:
        params = {
            "include_docs": "true",
            "startkey": json.dumps(prefix),
            "endkey": json.dumps(prefix + "￰"),
        }
        try:
            r = await self._client.get(f"/{db}/_all_docs", params=params)
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
        if r.status_code != 200:
            raise CouchDBDown(f"_all_docs: {r.status_code}")
        return [row["doc"] for row in r.json().get("rows", []) if row.get("doc")]

    async def changes(
        self, db: str, *, since: str = "0", heartbeat_ms: int = 30000
    ) -> AsyncIterator[dict]:
        """continuous `_changes` 구독 (§8-1). 하트비트 빈 줄은 건너뛴다."""
        params = {
            "feed": "continuous",
            "heartbeat": str(heartbeat_ms),
            "since": since,
            "include_docs": "false",
        }
        try:
            async with self._client.stream(
                "GET",
                f"/{db}/_changes",
                params=params,
                timeout=httpx.Timeout(10.0, read=None),
            ) as r:
                if r.status_code != 200:
                    raise CouchDBDown(f"_changes: {r.status_code}")
                async for line in r.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue  # heartbeat
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if "id" in row or "seq" in row:
                        yield row
        except httpx.HTTPError as exc:
            raise CouchDBDown(str(exc)) from exc
