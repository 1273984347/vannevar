# routers/context_card_router.py — 阶段1.6 Context Card 只读视图
import json
import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from auth import require_user
from context_card import SCHEMA_VERSION
from database import get_db
from models import SyncItem
from schemas import ContextCardListResponse, ContextCardOut

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/context-cards", tags=["context-cards"])

# sync_items.item_type → Context Card type 映射
# mood 是辅助元数据，不作为独立 Context Card 暴露
_ITEM_TYPE_TO_CARD_TYPE = {
    "snapshot": "focus-snapshot",
    "inspiration": "muse-inspiration",
}


@router.get("", response_model=ContextCardListResponse)
def list_context_cards(
    app: str = Query(default=None, description="按应用过滤：focus / muse"),
    # DRL-R1-V24: 添加分页参数，避免大量卡片一次性返回
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """列出当前用户的 Context Card（从 sync_items 包装为 v1 格式）。

    - 过滤掉 mood 类型（mood 是 Muse 辅助元数据，不是独立卡片）
    - 支持 app 参数过滤（focus / muse）
    - 按 server_updated_at 降序排列（最近同步的在前）
    - dev 模式（user_id=0）返回 admin 推送的数据
    """
    query = db.query(SyncItem).filter(
        SyncItem.owner_id == user_id,
        SyncItem.item_type.in_(_ITEM_TYPE_TO_CARD_TYPE.keys()),
    )
    if app:
        query = query.filter(SyncItem.app == app)

    # DRL-R1-V24: 应用分页
    rows = query.order_by(SyncItem.server_updated_at.desc()).offset(skip).limit(limit).all()

    items = []
    for row in rows:
        try:
            data = json.loads(row.data) if row.data else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("sync_item %d 的 data 字段 JSON 解析失败，跳过", row.id)
            continue
        card_type = _ITEM_TYPE_TO_CARD_TYPE.get(row.item_type)
        if not card_type:
            continue
        items.append(ContextCardOut(
            **{"schema": SCHEMA_VERSION},
            type=card_type,
            app=row.app,
            exportedAt=row.server_updated_at.isoformat() if row.server_updated_at else None,
            data=data,
            client_uuid=row.client_uuid,
        ))

    return ContextCardListResponse(count=len(items), items=items)
