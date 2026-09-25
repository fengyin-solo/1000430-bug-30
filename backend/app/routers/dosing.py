"""加药管理接口：维护加药单，覆盖开始投加、确认投加、撤销投加等动作。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, DosingSummary, EntryPayload, PageResult
from app.services.dosing import DosingService

router = APIRouter(prefix="/api/dosing", tags=["加药管理"])

service = DosingService()

LIST_FIELDS = ["加药单号", "药剂名称", "投加浓度", "投加量", "加药点位", "投加时间", "操作人员", "加药状态"]
STATUSES = ["待投加", "投加中", "已投加", "已撤销"]


@router.get("/summary", response_model=DosingSummary)
def summary() -> DosingSummary:
    """列表卡片汇总：待投加单、今日药剂用量（仅已投加）、撤销单数，口径以服务端为准。"""
    data = service.summary()
    return DosingSummary(**data)


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按加药单号检索"),
    status: str | None = Query(default=None, description="待投加、投加中、已投加、已撤销"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按加药单号与状态过滤加药管理列表；没有数据时返回空页，不报错。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    if status and status not in STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"状态「{status}」不支持，可选：{'、'.join(STATUSES)}",
        )
    items, total = service.list_entries(keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出加药管理清单：返回当前过滤条件下的全量数据。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "dosing", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条加药单明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"加药单 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条加药单；缺字段、投加量非法、单号重复都会逐条说明原因，不静默落库。"""
    entry, error = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=error or "加药单登记失败")
    return ActionResult(ok=True, message="加药单已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条加药单执行开始投加、确认投加、撤销投加；状态不符或重复提交会被拦下并说明原因。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
