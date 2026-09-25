"""加药管理业务规则：状态流转、字段校验与统计口径都收在这里。

口径约定（列表、统计、导出都按这一套来）：
- 待投加 / 投加中：单子还没完成，计入「待投加单」，不计入当日用量；
- 已投加：药剂已实际入水，是终态，计入「今日药剂用量」，不再接受任何动作；
- 已撤销：单据作废，是终态，只计入「撤销单数」，不计入当日用量，也不能再回到已投加；
  确需投加时只能用新单号重新登记。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.store import store

MODULE = "dosing"
REQUIRED_FIELDS = ["加药单号", "药剂名称", "投加浓度"]
STATUS_ORDER = ["待投加", "投加中", "已投加", "已撤销"]
ACTION_RULES = {"开始投加": "投加中", "确认投加": "已投加", "撤销投加": "已撤销"}

# 每个状态允许执行的动作；已投加、已撤销是终态，不在表里的组合一律拦截。
TRANSITIONS: dict[str, set[str]] = {
    "待投加": {"开始投加", "确认投加", "撤销投加"},
    "投加中": {"确认投加", "撤销投加"},
    "已投加": set(),
    "已撤销": set(),
}
OPEN_STATUSES = {"待投加", "投加中"}


class DosingService:
    def __init__(self) -> None:
        # 校验单号重复和写入必须是一步，避免并发双击落出两条同号记录。
        self._lock = threading.Lock()

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        chemical: str | None = None,
        concentration: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("加药单号", ""))]
        if chemical:
            rows = [row for row in rows if chemical in str(row.get("药剂名称", ""))]
        if concentration:
            rows = [row for row in rows if concentration in str(row.get("投加浓度", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def summary(self) -> dict[str, Any]:
        """统计卡片口径：基于全量单据计算，与列表筛选条件无关，保证来回进页面都对得上。"""
        rows = store.rows(MODULE)
        today = date.today().isoformat()
        today_amount = 0.0
        for row in rows:
            # 只有已投加才计入当日用量；已撤销、待投加、投加中一律不算。
            if row.get("status") != "已投加":
                continue
            if str(row.get("投加时间") or "")[:10] != today:
                continue
            try:
                today_amount += float(row.get("投加量") or 0)
            except (TypeError, ValueError):
                continue  # 历史样例数据里投加量不是数字，跳过不计
        return {
            "pending": sum(1 for row in rows if row.get("status") in OPEN_STATUSES),
            "today_amount": round(today_amount, 3),
            "canceled": sum(1 for row in rows if row.get("status") == "已撤销"),
        }

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        errors = self._validate(values)
        if errors:
            return None, errors
        serial = str(values.get("加药单号") or "").strip()
        with self._lock:
            for row in store.rows(MODULE):
                if str(row.get("加药单号", "")).strip() == serial:
                    return None, [
                        f"加药单号 {serial} 已登记过（当前状态：{row.get('status')}），"
                        "同一单号只保留一条记录，本次重复提交已被拦截，未生成新记录"
                    ]
            rows = store.rows(MODULE)
            entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            entry.update({field: str(values.get(field)).strip() for field in REQUIRED_FIELDS})
            for field in ("投加量", "加药点位", "投加时间", "操作人员"):
                value = values.get(field)
                if value is not None and str(value).strip():
                    entry[field] = str(value).strip()
            entry["status"] = STATUS_ORDER[0]
            entry["pending"] = True
            entry["abnormal"] = False
            rows.append(entry)
        return entry, []

    def _validate(self, values: dict[str, Any]) -> list[str]:
        """登记前的字段校验：每条错误都说明原因，且明确本次登记未生效。"""
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return [
                f"缺少必填字段：{'、'.join(missing)}；单号用于追溯、药剂与浓度用于核算配药比例，"
                "都不能为空，本次登记未生效，请补齐后重新提交"
            ]
        raw_amount = str(values.get("投加量") or "").strip()
        if raw_amount:
            try:
                amount = float(raw_amount)
            except ValueError:
                return [f"投加量需为数字（收到「{raw_amount}」），本次登记未生效，请修正后重新提交"]
            if amount < 0:
                return [
                    f"投加量不能为负（收到 {raw_amount}），负值会把当日合计算错，"
                    "本次登记未生效，请修正后重新提交"
                ]
        return []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于加药管理可执行范围"
        with self._lock:
            entry = store.find(MODULE, entry_id)
            if entry is None:
                return None, f"加药单 {entry_id} 不存在或已归档"
            current = str(entry.get("status") or "")
            if action not in TRANSITIONS.get(current, set()):
                return None, self._reject_reason(current, action)
            target = ACTION_RULES[action]
            entry["status"] = target
            entry["pending"] = target in OPEN_STATUSES
            entry["abnormal"] = target == "已撤销"
            if action == "确认投加":
                # 确认时把投加时间落成当天，当日合计才能和已投加记录对上。
                entry["投加时间"] = date.today().isoformat()
        return entry, f"加药单已{action}"

    @staticmethod
    def _reject_reason(current: str, action: str) -> str:
        if current == "已撤销":
            return (
                f"加药单已撤销，单据作废后不能再{action}、状态不会回到已投加；"
                "确需投加请用新单号重新登记"
            )
        if current == "已投加":
            return f"加药单已确认投加并计入当日用量，{action}不再生效，请勿重复操作"
        return f"当前状态「{current}」不允许{action}，请刷新列表后按可用动作操作"
