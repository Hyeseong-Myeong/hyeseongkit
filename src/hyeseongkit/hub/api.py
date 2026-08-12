"""HTTP API (설계서 §3) — 에러 바디 {error, message, detail}, Bearer 인증."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict

from .errors import ApiError


def _bearer(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def device_auth(request: Request) -> dict:
    return await request.app.state.auth.verify_device(_bearer(request))


def admin_auth(request: Request) -> None:
    request.app.state.auth.verify_admin(_bearer(request))


DeviceDep = Annotated[dict, Depends(device_auth)]

router = APIRouter()


# ── 요청 모델 (§3-3~§3-5, §3-10) — 오탈자 필드는 400 SCHEMA_INVALID ──


class PushBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread: str | None = None
    project_id: str
    title: str
    slug: str | None = None  # 작업 주제 영문 요약 — thread ID에 쓰임 (2026-08-12 사용자 결정)
    sections: dict[str, str]
    sensitivity: str | None = None
    tool: str = "manual"
    model: str | None = None
    device: str
    reopen: bool = False
    mask_report: list[str] = []


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    rationale: str | None = None
    rejected: str | None = None
    date: str | None = None


class DecideBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread: str
    project_id: str
    decision: DecisionBody
    tool: str = "manual"
    model: str | None = None
    device: str
    mask_report: list[str] = []


class GitState(BaseModel):
    model_config = ConfigDict(extra="forbid")
    branch: str | None = None
    head: str | None = None
    dirty: int = 0


class CheckpointBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread: str | None = None
    project_id: str
    reason: str = "manual"
    git: GitState | None = None
    transcript_path: str | None = None
    tool: str = "manual"
    model: str | None = None
    device: str


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    thread: str
    project_id: str | None = None
    outcome: str = "done"
    tool: str = "manual"
    device: str


class ProjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_id: str
    canonical: str = ""
    name: str = ""
    sensitivity: str = "tech"


class AliasBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical: str


class DeviceAddBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_id: str
    name: str = ""


# ── 상태·인증 ────────────────────────────────────────────────


@router.get("/healthz")
async def healthz(request: Request) -> dict:
    """생존 확인 — 인증 없음. CouchDB ping 포함 (§3-2)."""
    from .. import __version__

    ok = await request.app.state.couch.ping()
    return {"status": "ok", "version": __version__, "couchdb": "ok" if ok else "down"}


@router.get("/v1/whoami")
async def whoami(device: DeviceDep) -> dict:
    return {
        "device_id": device.get("device_id"),
        "name": device.get("name"),
        "scopes": device.get("scopes", []),
    }


# ── 세션 (§3-3~§3-9) ────────────────────────────────────────


@router.post("/v1/session/push", status_code=201)
async def push(body: PushBody, device: DeviceDep, request: Request) -> dict:
    return await request.app.state.service.push(device, body.model_dump())


@router.post("/v1/session/decide", status_code=201)
async def decide(body: DecideBody, device: DeviceDep, request: Request) -> dict:
    return await request.app.state.service.decide(device, body.model_dump())


@router.post("/v1/session/checkpoint", status_code=201)
async def checkpoint(body: CheckpointBody, device: DeviceDep, request: Request):
    result = await request.app.state.service.checkpoint(device, body.model_dump())
    if result is None:
        return Response(status_code=204)  # active 없음 → 무시. 훅은 실패하지 않는다 (§3-5)
    return result


@router.post("/v1/session/close", status_code=201)
async def close(body: CloseBody, device: DeviceDep, request: Request) -> dict:
    return await request.app.state.service.close(device, body.model_dump())


@router.get("/v1/session/resume")
async def resume(
    request: Request,
    _device: DeviceDep,
    thread: str | None = None,
    last: int = 0,
    project_id: str | None = None,
    budget: int = 2000,
    format: str = "packet",  # noqa: A002 — 쿼리 파라미터 이름은 스펙 고정 (§3-6)
    events: int = 0,
) -> dict:
    return await request.app.state.service.resume(
        thread=thread,
        last=bool(last),
        project_id=project_id,
        budget=budget,
        fmt=format,
        events=events,
    )


@router.get("/v1/session/status")
async def status(request: Request, _device: DeviceDep, project_id: str | None = None) -> dict:
    return await request.app.state.service.status(project_id)


@router.get("/v1/session/search")
async def search(
    request: Request,
    _device: DeviceDep,
    q: str = Query(...),
    project_id: str | None = None,
    status: str | None = None,
    limit: int = 10,
) -> dict:
    return await request.app.state.service.search(
        q, project_id=project_id, status=status, limit=limit
    )


# ── 프로젝트 (§3-10) ────────────────────────────────────────


@router.post("/v1/projects")
async def project_register(
    body: ProjectBody, _device: DeviceDep, request: Request, response: Response
) -> dict:
    doc, created = await request.app.state.service.project_register(body.model_dump())
    response.status_code = 201 if created else 200
    return doc


@router.get("/v1/projects")
async def project_find(
    request: Request,
    _device: DeviceDep,
    canonical: str | None = None,
    name: str | None = None,
) -> dict:
    docs = await request.app.state.service.project_find(canonical=canonical, name=name)
    return {"projects": docs}


@router.get("/v1/projects/{project_id}")
async def project_get(project_id: str, _device: DeviceDep, request: Request) -> dict:
    return await request.app.state.service.project_get(project_id)


@router.post("/v1/projects/{project_id}/aliases")
async def project_add_alias(
    project_id: str, body: AliasBody, _device: DeviceDep, request: Request
) -> dict:
    return await request.app.state.service.project_add_alias(project_id, body.canonical)


# ── admin (§5-2) ────────────────────────────────────────────


@router.post("/v1/admin/devices", status_code=201, dependencies=[Depends(admin_auth)])
async def device_add(body: DeviceAddBody, request: Request) -> dict:
    doc, token = await request.app.state.auth.add_device(body.device_id, body.name)
    return {"device_id": doc["device_id"], "token": token}  # 원문 토큰은 이 응답에서만 노출


@router.get("/v1/admin/devices", dependencies=[Depends(admin_auth)])
async def device_list(request: Request) -> dict:
    return {"devices": await request.app.state.auth.list_devices()}


@router.delete("/v1/admin/devices/{device_id}", dependencies=[Depends(admin_auth)])
async def device_revoke(device_id: str, request: Request) -> dict:
    doc = await request.app.state.auth.revoke_device(device_id)
    return {"device_id": doc["device_id"], "revoked": True}


__all__ = ["router", "ApiError", "device_auth", "admin_auth"]
