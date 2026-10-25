# routers/chat_router.py — Phase 6.J AI 助手对话化端点
"""提供 AI 助手多轮对话 + 持久化历史的 API 端点。

端点：
- POST   /api/projects/{project_id}/conversations         创建会话
- GET    /api/projects/{project_id}/conversations         列出项目下当前用户的会话
- GET    /api/conversations/{conversation_id}/messages    列出会话内消息
- POST   /api/conversations/{conversation_id}/ask         SSE 流式回答（加入 SSE 白名单）
- DELETE /api/conversations/{conversation_id}             软删除会话

设计要点：
- 鉴权用 require_admin（与 /demo、/demo/story 一致），demo 演示 顺畅
- SSE 流式回答前先存 user 消息，流式结束后存 assistant 消息
- AI 三层降级：demo 模式 / 无配置（is_fallback=True）/ API 失败（is_fallback=False，已知残留）
  · 已知残留：API 失败时若已 yield 部分 LLM 内容，is_fallback 误标记为 False
  · demo 演示 路径不触发（demo 模式 bypass），发生概率低，见 backlog
- 软删除：deleted_at 非空表示已删除，列表/消息查询过滤
"""
import json as _json
import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session, selectinload

from ai import answer_conversation_stream, is_demo_mode
from auth import require_admin
from database import get_db
from models import (
    ChatMessage,
    Conversation,
    Handoff,
    Message,
    Project,
    ProjectContextInspiration,
    ProjectContextSnapshot,
    Task,
)
from schemas import (
    ChatAskRequest,
    ChatMessageListResponse,
    ChatMessageOut,
    ConversationCreate,
    ConversationListResponse,
    ConversationOut,
)

logger = logging.getLogger(__name__)
router = APIRouter(tags=["chat"])


# 每用户每项目最大会话数，防止滥用
MAX_CONVERSATIONS_PER_PROJECT = 10
# 单会话最大历史消息数（超出后旧消息仍保留，但 LLM 上下文只取最近 10 条）
MAX_MESSAGES_PER_CONVERSATION = 200


def _build_project_data(
    project: Project,
    snapshots: Optional[List[ProjectContextSnapshot]] = None,
    inspirations: Optional[List[ProjectContextInspiration]] = None,
) -> dict:
    """从 Project ORM 构造 ai 模块需要的 project_data 字典。

    Phase 7.4 (建议 1): 注入 Focus 快照 + Muse 灵感到 project_data，
    供 ai.py 的 _build_proactive_context 构造引用上下文，让 AI 回答时
    可使用 [ref:snapshot:N] / [ref:inspiration:N] 标记引用具体卡片。
    """
    tasks = list(project.tasks) if project.tasks else []
    messages = list(project.messages) if project.messages else []
    handoffs = list(project.handoffs) if project.handoffs else []

    pending_tasks = [t for t in tasks if t.status != "done"]
    done_count = sum(1 for t in tasks if t.status == "done")

    data = {
        "name": project.name,
        "status": project.status,
        "total_tasks": len(tasks),
        "done_tasks": done_count,
        "pending_tasks": [
            {"title": t.title, "owner": t.owner_name, "status": t.status}
            for t in pending_tasks
        ],
        "recent_messages": [
            {"author": m.author, "content": m.content[:200]}
            for m in messages[-10:]
        ],
        "recent_handoffs": [
            {
                "summary": h.summary[:200],
                "outgoing": h.outgoing_volunteer,
                "incoming": h.incoming_volunteer,
            }
            for h in handoffs[-3:]
        ],
    }

    # Phase 7.4 (建议 1): 注入 Context Cards（Focus 快照 + Muse 灵感）
    # 只保留 id/title/summary/note/mood 等关键字段，避免 prompt 过长
    if snapshots is not None:
        data["snapshots"] = [
            {
                "id": s.id,
                "title": s.title,
                "summary": (s.summary or "")[:200],
                "tab_count": s.tab_count,
            }
            for s in snapshots
        ]
    if inspirations is not None:
        data["inspirations"] = [
            {
                "id": i.id,
                "title": i.title,
                "note": (i.note or "")[:200],
                "mood": i.mood or "",
            }
            for i in inspirations
        ]
    return data


def _get_conversation_or_404(
    db: Session,
    conversation_id: int,
    include_deleted: bool = False,
) -> Conversation:
    """获取会话，不存在或已软删除（include_deleted=False 时）抛 404。"""
    conv = db.query(Conversation).filter(Conversation.id == conversation_id).first()
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if not include_deleted and conv.deleted_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    return conv


# ============================================================
# 端点
# ============================================================


@router.post(
    "/api/projects/{project_id}/conversations",
    response_model=ConversationOut,
    status_code=201,
)
def create_conversation(
    project_id: int,
    payload: Optional[ConversationCreate] = None,
    request: Request = None,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """创建新会话。

    每用户每项目限制 MAX_CONVERSATIONS_PER_PROJECT 个活跃会话。
    """
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 检查活跃会话数（dev 模式 admin user_id=0，与其他 admin 共享计数）
    active_count = (
        db.query(Conversation)
        .filter(
            Conversation.project_id == project_id,
            Conversation.deleted_at.is_(None),
        )
        .count()
    )
    if active_count >= MAX_CONVERSATIONS_PER_PROJECT:
        raise HTTPException(
            status_code=429,
            detail=f"每项目最多 {MAX_CONVERSATIONS_PER_PROJECT} 个活跃会话，请先删除旧会话",
        )

    title = (payload.title if payload and payload.title else "新会话")[:200]
    # dev 模式 owner_id=0（admin Basic Auth 无 user_id）
    conv = Conversation(
        project_id=project_id,
        title=title,
        owner_id=0,
    )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return ConversationOut.model_validate(conv)


@router.get(
    "/api/projects/{project_id}/conversations",
    response_model=ConversationListResponse,
)
def list_conversations(
    project_id: int,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """列出项目下所有活跃会话（按 created_at 倒序）。"""
    project = db.query(Project).filter(Project.id == project_id).first()
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    convs = (
        db.query(Conversation)
        .filter(
            Conversation.project_id == project_id,
            Conversation.deleted_at.is_(None),
        )
        .order_by(Conversation.created_at.desc())
        .all()
    )
    return ConversationListResponse(
        count=len(convs),
        items=[ConversationOut.model_validate(c) for c in convs],
    )


@router.get(
    "/api/conversations/{conversation_id}/messages",
    response_model=ChatMessageListResponse,
)
def list_messages(
    conversation_id: int,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """列出会话内所有消息（按 id 升序）。"""
    conv = _get_conversation_or_404(db, conversation_id)
    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.id.asc())
        .all()
    )
    return ChatMessageListResponse(
        count=len(msgs),
        items=[ChatMessageOut.model_validate(m) for m in msgs],
    )


@router.post("/api/conversations/{conversation_id}/ask")
async def ask_conversation(
    conversation_id: int,
    payload: ChatAskRequest,
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """SSE 流式回答用户问题。

    流程：
    1. 校验会话存在 + 项目存在
    2. 持久化 user 消息
    3. 加载历史消息（最近 10 条）作为 LLM 上下文
    4. 流式 yield assistant 回答
    5. 流式结束后持久化 assistant 消息（含 is_fallback 标记）

    SSE 事件格式（与 handoffs/stream-generate 一致）：
    - event: delta  data: {"delta": "..."}
    - event: done   data: {"message_id": N, "is_fallback": bool}
    - event: error  data: {"message": "..."}
    """
    conv = _get_conversation_or_404(db, conversation_id)
    project = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.messages),
            selectinload(Project.handoffs),
        )
        .filter(Project.id == conv.project_id)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 检查消息数上限
    msg_count = (
        db.query(ChatMessage)
        .filter(ChatMessage.conversation_id == conversation_id)
        .count()
    )
    if msg_count >= MAX_MESSAGES_PER_CONVERSATION:
        raise HTTPException(
            status_code=429,
            detail=f"会话消息数已达上限 {MAX_MESSAGES_PER_CONVERSATION}，请新建会话",
        )

    # 持久化 user 消息
    user_msg = ChatMessage(
        conversation_id=conversation_id,
        role="user",
        content=payload.question,
        is_fallback=False,
    )
    db.add(user_msg)
    db.commit()
    db.refresh(user_msg)

    # 加载历史消息（不含当前 user_msg，取最近 10 条作为上下文）
    history_msgs = (
        db.query(ChatMessage)
        .filter(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.id < user_msg.id,
        )
        .order_by(ChatMessage.id.asc())
        .all()
    )
    history = [{"role": m.role, "content": m.content} for m in history_msgs[-10:]]

    # Phase 7.4 (建议 1): 查询项目关联的最近 5 条 Focus 快照 + Muse 灵感，
    # 注入到 project_data 供 AI 在回答时引用（[ref:snapshot:N] / [ref:inspiration:N]）
    snapshots = (
        db.query(ProjectContextSnapshot)
        .filter(ProjectContextSnapshot.project_id == conv.project_id)
        .order_by(ProjectContextSnapshot.created_at.desc())
        .limit(5)
        .all()
    )
    inspirations = (
        db.query(ProjectContextInspiration)
        .filter(ProjectContextInspiration.project_id == conv.project_id)
        .order_by(ProjectContextInspiration.created_at.desc())
        .limit(5)
        .all()
    )
    project_data = _build_project_data(project, snapshots, inspirations)

    # 标题自动更新：若会话标题仍是默认值，用问题前 30 字截断更新
    if conv.title == "新会话":
        new_title = payload.question[:30].strip() or "新会话"
        if len(payload.question) > 30:
            new_title += "…"
        conv.title = new_title[:200]
        db.add(conv)
        db.commit()

    async def event_stream():
        collected_chunks: List[str] = []
        is_fallback = False

        try:
            async for delta in answer_conversation_stream(
                project_data, history, payload.question
            ):
                collected_chunks.append(delta)
                data_str = _json.dumps({"delta": delta}, ensure_ascii=False)
                yield f"event: delta\ndata: {data_str}\n\n"

            # 流结束：若未收集到任何内容，使用降级文本
            if not collected_chunks:
                from ai import build_chat_fallback_answer
                fallback_text = build_chat_fallback_answer(project_data, payload.question)
                collected_chunks.append(fallback_text)
                is_fallback = True
                data_str = _json.dumps({"delta": fallback_text, "fallback": True}, ensure_ascii=False)
                yield f"event: delta\ndata: {data_str}\n\n"
            else:
                # 判断是否为降级（demo 模式不计降级，无 API 配置计降级）
                # answer_conversation_stream 在 cfg is None 时 yield 降级文本
                # 简化：若未配置 AI 且非 demo 模式，标记 is_fallback
                from ai import _get_ai_config
                if not is_demo_mode() and _get_ai_config() is None:
                    is_fallback = True

            # 持久化 assistant 消息
            full_content = "".join(collected_chunks)
            # 安全截断
            if len(full_content) > 4000:
                full_content = full_content[:3997] + "…"

            assistant_msg = ChatMessage(
                conversation_id=conversation_id,
                role="assistant",
                content=full_content,
                is_fallback=is_fallback,
            )
            db.add(assistant_msg)
            db.commit()
            db.refresh(assistant_msg)

            done_data = {
                "message_id": assistant_msg.id,
                "is_fallback": is_fallback,
            }
            yield f"event: done\ndata: {_json.dumps(done_data, ensure_ascii=False)}\n\n"

        except Exception as e:
            logger.warning("AI 对话流式回答异常: %s", e)
            # 即使异常，已收集的内容也持久化
            if collected_chunks:
                try:
                    full_content = "".join(collected_chunks)
                    if len(full_content) > 4000:
                        full_content = full_content[:3997] + "…"
                    assistant_msg = ChatMessage(
                        conversation_id=conversation_id,
                        role="assistant",
                        content=full_content,
                        is_fallback=True,
                    )
                    db.add(assistant_msg)
                    db.commit()
                except Exception:
                    pass
            err_str = _json.dumps({"message": "生成失败，请重试"}, ensure_ascii=False)
            yield f"event: error\ndata: {err_str}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: int,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """软删除会话（设置 deleted_at）。"""
    conv = _get_conversation_or_404(db, conversation_id)
    from models import _now
    conv.deleted_at = _now()
    db.add(conv)
    db.commit()
    return {"deleted": True, "id": conversation_id}
