"""context_card.py — Vannevar Context Card v1 Python 版。

与 shared/vannevar-context-card.js 行为对齐，供 Bridge 服务在交接表单中
导入 Focus 快照 / Muse 灵感 / Bridge 交接卡片，把内容作为交接背景信息。

只读：仅做解析 + 校验，不写入数据库。端点写入由调用方决定。
"""
from __future__ import annotations

import json
from typing import Any, Dict

SCHEMA_VERSION = "vannevar-context-card/v1"
FILE_EXTENSION = ".vcf"

# type -> app 允许映射
_TYPE_APP_MAP: Dict[str, str] = {
    "focus-snapshot": "focus",
    "muse-inspiration": "muse",
    "bridge-handoff": "bridge",
}

# 每种 type 必填的 data 字段
_REQUIRED_FIELDS: Dict[str, tuple] = {
    "focus-snapshot": ("title", "tabs", "createdAt"),
    "muse-inspiration": ("title", "createdAt"),
    "bridge-handoff": ("projectName", "summary", "createdAt"),
}


class ContextCardError(ValueError):
    """Context Card 解析或校验失败时抛出。

    继承 ValueError 便于上层用 HTTPException(422, detail=str(err)) 统一处理。
    """


def _require_dict(obj: Any, label: str) -> dict:
    if not isinstance(obj, dict):
        raise ContextCardError(f"{label} 必须是对象")
    return obj


def validate_card(card: Any) -> dict:
    """校验 Context Card 格式，返回校验通过的 dict。

    Args:
        card: 待校验的对象（通常是 json.loads 的结果）

    Returns:
        校验通过的 card dict

    Raises:
        ContextCardError: 任何字段不合法时抛出
    """
    card = _require_dict(card, "卡片")
    if card.get("schema") != SCHEMA_VERSION:
        raise ContextCardError(f"schema 必须为 {SCHEMA_VERSION}，收到 {card.get('schema')!r}")
    type_ = card.get("type")
    if type_ not in _TYPE_APP_MAP:
        raise ContextCardError(f"未知的 type：{type_!r}")
    expected_app = _TYPE_APP_MAP[type_]
    if card.get("app") != expected_app:
        raise ContextCardError(
            f"type {type_!r} 应搭配 app {expected_app!r}，收到 {card.get('app')!r}"
        )
    exported_at = card.get("exportedAt")
    if not isinstance(exported_at, str) or not exported_at:
        raise ContextCardError("exportedAt 必须为非空字符串")
    data = _require_dict(card.get("data"), "data")
    for field in _REQUIRED_FIELDS[type_]:
        if field not in data or data[field] is None:
            raise ContextCardError(f"data 缺少必填字段：{field}")
    return card


def parse_context_card(json_str: str) -> dict:
    """反序列化 JSON 字符串为 Context Card，并完成格式校验。

    Args:
        json_str: Context Card JSON 字符串

    Returns:
        校验通过的 card dict

    Raises:
        ContextCardError: JSON 解析失败或格式校验失败时抛出
    """
    if not isinstance(json_str, str):
        raise ContextCardError("反序列化输入必须是字符串")
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as err:
        raise ContextCardError(f"JSON 解析失败：{err.msg}") from err
    return validate_card(parsed)


def card_to_background_text(card: dict) -> str:
    """把 Context Card 的 data 载荷转换为交接摘要背景信息文本。

    - focus-snapshot: 列出标签页数量与每个标签页标题 + URL
    - muse-inspiration: 列出灵感标题、来源、备注、选中片段、氛围
    - bridge-handoff: 列出原项目名、原交接摘要、任务列表

    Args:
        card: 校验通过的 Context Card dict

    Returns:
        适合填入交接摘要 textarea 的多行文本（<=2000 字符，与 HandoffCreate.summary 约束一致）
    """
    type_ = card.get("type")
    data = card.get("data") or {}
    lines: list[str] = []
    if type_ == "focus-snapshot":
        title = str(data.get("title") or "未命名上下文")
        tabs = data.get("tabs") if isinstance(data.get("tabs"), list) else []
        lines.append(f"【来自 Focus 快照】{title}")
        lines.append(f"标签页数量：{len(tabs)}")
        for i, tab in enumerate(tabs[:20], start=1):
            if not isinstance(tab, dict):
                continue
            tab_title = str(tab.get("title") or "(无标题)")
            tab_url = str(tab.get("url") or "")
            lines.append(f"  {i}. {tab_title}")
            if tab_url:
                lines.append(f"     {tab_url}")
        if len(tabs) > 20:
            lines.append(f"  …还有 {len(tabs) - 20} 个标签页")
    elif type_ == "muse-inspiration":
        title = str(data.get("title") or "未命名灵感")
        lines.append(f"【来自 Muse 灵感】{title}")
        url = data.get("url")
        if url:
            lines.append(f"来源：{url}")
        mood = data.get("mood")
        if mood:
            lines.append(f"氛围：{mood}")
        note = data.get("note")
        if note:
            lines.append(f"备注：{note}")
        selected = data.get("selectedText")
        if selected:
            snippet = selected if len(selected) <= 300 else selected[:297] + "…"
            lines.append(f"选中片段：{snippet}")
    elif type_ == "bridge-handoff":
        project_name = str(data.get("projectName") or "未命名项目")
        lines.append(f"【来自 Bridge 交接】{project_name}")
        if data.get("outgoing") or data.get("incoming"):
            lines.append(
                f"交接人：{data.get('outgoing') or '?'} → {data.get('incoming') or '?'}"
            )
        summary = data.get("summary")
        if summary:
            lines.append(f"原摘要：{summary}")
        tasks = data.get("tasks") if isinstance(data.get("tasks"), list) else []
        if tasks:
            lines.append(f"任务列表（{len(tasks)}）：")
            for i, task in enumerate(tasks[:20], start=1):
                if not isinstance(task, dict):
                    continue
                task_title = str(task.get("title") or "(无标题)")
                owner = task.get("owner_name") or "未分配"
                status = task.get("status") or "?"
                lines.append(f"  {i}. [{status}] {task_title}（{owner}）")
            if len(tasks) > 20:
                lines.append(f"  …还有 {len(tasks) - 20} 个任务")
    else:
        lines.append(f"【未知卡片类型】{type_}")

    text = "\n".join(lines)
    # 与 HandoffCreate.summary max_length=2000 保持一致，超出截断
    if len(text) > 2000:
        text = text[:1997] + "…"
    return text
