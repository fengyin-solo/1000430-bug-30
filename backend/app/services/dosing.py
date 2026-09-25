"""加药管理业务规则：状态流转、字段校验与筛选口径都收在这里。

状态口径（与列表「加药状态」展示字段始终保持一致）：
- 待投加 → 投加中 → 已投加 是正常流转链；
- 「已撤销」是终态：撤销只用于撤回一次尚未真正发生的投加，撤销后不能再
  直接确认投加，必须按正常链路重新开始，避免同一单号先撤销又回到已投加；
- 「已投加」也是终态：投加事实已经登记，不允许撤销抹掉，只能走后续更正流程。
"""
from __future__ import annotations

import math
from datetime import date
from typing import Any

from app.store import store

MODULE = "dosing"
REQUIRED_FIELDS = ["加药单号", "药剂名称", "投加浓度"]
OPTIONAL_FIELDS = ["投加量", "加药点位", "投加时间", "操作人员"]
STATUS_PENDING = "待投加"
STATUS_RUNNING = "投加中"
STATUS_DONE = "已投加"
STATUS_CANCELLED = "已撤销"
STATUS_ORDER = [STATUS_PENDING, STATUS_RUNNING, STATUS_DONE, STATUS_CANCELLED]

# 每个动作只允许从指定当前状态发起；已投加是终态不在任何动作的前置里。
ACTION_RULES: dict[str, dict[str, Any]] = {
    # 待投加正常开始；已撤销单可以凭原单号重新开始（保留既有登记信息，不新建记录）。
    "开始投加": {"target": STATUS_RUNNING, "allow_from": {STATUS_PENDING, STATUS_CANCELLED}},
    "确认投加": {"target": STATUS_DONE, "allow_from": {STATUS_RUNNING}},
    # 只有还没真正投下去（待投加/投加中）才允许撤销；已投加不能撤销。
    "撤销投加": {
        "target": STATUS_CANCELLED,
        "allow_from": {STATUS_PENDING, STATUS_RUNNING},
    },
}


class DosingService:
    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("加药单号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        """登记加药单。

        返回 (记录, 错误说明)：成功时错误说明为 None；失败时记录为 None，
        错误说明逐条列出原因（缺字段、投加量非法、单号重复），调用方不再只回
        一句笼统的「缺少必填」。
        """
        errors: list[str] = []

        cleaned: dict[str, Any] = {}
        for field in REQUIRED_FIELDS:
            text = str(values.get(field) or "").strip()
            if not text:
                errors.append(f"缺少必填字段：{field}")
            else:
                cleaned[field] = text

        amount, amount_error = self._parse_amount(values.get("投加量"))
        if amount_error:
            errors.append(amount_error)
        elif amount is not None:
            cleaned["投加量"] = amount

        for field in OPTIONAL_FIELDS[1:]:
            text = str(values.get(field) or "").strip()
            if text:
                cleaned[field] = text

        code = cleaned.get("加药单号")
        if code:
            duplicate = next(
                (row for row in store.rows(MODULE) if str(row.get("加药单号", "")).strip() == code),
                None,
            )
            if duplicate is not None:
                # 同一加药单号是业务唯一键：重复登记直接拦下，避免出现两条同号记录。
                errors.append(f"加药单号 {code} 已存在（记录 {duplicate.get('id')}），不能重复登记")

        if errors:
            return None, "；".join(errors)

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(cleaned)
        entry["status"] = STATUS_PENDING
        entry["pending"] = True
        entry["abnormal"] = False
        # 展示列「加药状态」与内部 status 同口径，避免列表两列状态对不上。
        entry["加药状态"] = STATUS_PENDING
        rows.append(entry)
        return entry, None

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"加药单 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于加药管理可执行范围"

        rule = ACTION_RULES[action]
        current = str(entry.get("status") or "")
        target = rule["target"]

        if current == target:
            return None, f"加药单当前已是「{current}」状态，请勿重复提交「{action}」"
        if current not in rule["allow_from"]:
            hint = self._blocked_reason(current, action, target)
            return None, hint

        entry["status"] = target
        entry["加药状态"] = target
        entry["pending"] = target not in (STATUS_DONE, STATUS_CANCELLED)
        # 撤销单重新开始后不再算异常；撤销时才标记异常。
        entry["abnormal"] = target == STATUS_CANCELLED
        if action == "确认投加":
            # 投加事实在确认时落时间戳，作为「当日合计」的统计依据。
            entry.setdefault("投加时间", date.today().isoformat())
        return entry, f"加药单已{action}"

    def summary(self, *, today: str | None = None) -> dict[str, int | float]:
        """列表顶部三张卡片的统一口径，全部只看服务端数据。

        - 待投加单：status=待投加（含投加中视为在途，这里卡片只数尚未开始的）；
        - 今日药剂用量：只统计 status=已投加 且投加时间为当日的投加量之和，
          已撤销单一律不计入——撤销意味着投加事实不成立，不能再计入当日合计；
        - 撤销单数：status=已撤销 的单数。
        """
        day = today or date.today().isoformat()
        rows = store.rows(MODULE)
        pending = sum(1 for row in rows if row.get("status") == STATUS_PENDING)
        cancelled = sum(1 for row in rows if row.get("status") == STATUS_CANCELLED)
        today_total = 0.0
        for row in rows:
            if row.get("status") != STATUS_DONE:
                continue
            if not str(row.get("投加时间") or "").startswith(day):
                continue
            amount, _ = self._parse_amount(row.get("投加量"))
            if amount is not None:
                today_total += amount
        today_total = round(today_total, 4)
        if today_total == int(today_total):
            today_total = int(today_total)
        return {
            "pending": pending,
            "cancelled": cancelled,
            "today_total": today_total,
            "today": day,
        }

    @staticmethod
    def _parse_amount(raw: Any) -> tuple[float | None, str | None]:
        """投加量允许留空；一旦填写必须是不小于 0 的数字，负数/非数字一律拒绝。"""
        if raw is None or str(raw).strip() == "":
            return None, None
        try:
            amount = float(raw)
        except (TypeError, ValueError):
            return None, "投加量必须是不小于 0 的数字（单位与药剂浓度保持一致）"
        if not math.isfinite(amount):
            return None, "投加量必须是有限数字，不能填写 NaN 或无穷大"
        if amount < 0:
            return None, "投加量不能为负数：请核对实际投加量后重新提交"
        return amount, None

    @staticmethod
    def _blocked_reason(current: str, action: str, target: str) -> str:
        if current == STATUS_DONE:
            return (
                f"加药单已投加，投加事实已计入当日合计，不能再执行「{action}」；"
                "如投加数据有误请走更正流程，不能直接撤销"
            )
        if current == STATUS_CANCELLED:
            return (
                "加药单已撤销，撤销是终态，不能直接「确认投加」回到已投加；"
                "请先执行「开始投加」重新发起投加（原单号与登记信息保留）"
            )
        return f"加药单当前为「{current}」状态，不允许执行「{action}」（目标状态：{target}）"
