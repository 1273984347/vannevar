# routers/sync_router.py — 阶段1.5 跨设备数据同步 API（push/pull）
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import require_user
from database import get_db
from models import SyncItem
from schemas import (
    ALLOWED_SYNC_APPS,
    SyncItemOut,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
    SyncPushResult,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sync", tags=["sync"])


@router.post("/push", response_model=SyncPushResponse)
def sync_push(
    payload: SyncPushRequest,
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """批量推送本地数据到云端。

    - 用 (owner_id, app, client_uuid) 去重
    - 已存在记录：client_updated_at 更新才覆盖（LWW），否则跳过
    - 不存在记录：新建

    dev 模式（user_id=0）也允许推送，用于开发调试。
    """
    # DRL-R1-V4: 限制单次推送 items 数量，防止超大 payload 导致 DoS
    if len(payload.items) > 500:
        raise HTTPException(status_code=413, detail="单次推送 items 数量不能超过 500")

    created = 0
    updated = 0
    skipped = 0
    results: list[SyncPushResult] = []

    for item in payload.items:
        # DRL-R1-V3: 兜底校验 app 字段（schema 已校验，此处防御纵深）
        if item.app not in ALLOWED_SYNC_APPS:
            raise HTTPException(status_code=422, detail=f"不支持的 app: {item.app}")
        existing = (
            db.query(SyncItem)
            .filter(
                SyncItem.owner_id == user_id,
                SyncItem.app == item.app,
                SyncItem.client_uuid == item.client_uuid,
            )
            .first()
        )

        if existing is None:
            # 新建
            db.add(SyncItem(
                owner_id=user_id,
                app=item.app,
                item_type=item.item_type,
                client_uuid=item.client_uuid,
                data=json.dumps(item.data, ensure_ascii=False),
                client_created_at=item.client_created_at,
                client_updated_at=item.client_updated_at,
            ))
            created += 1
            results.append(SyncPushResult(
                client_uuid=item.client_uuid,
                action="created",
            ))
        # DRL-R1-V5: LWW 修复——existing.client_updated_at 为 None 时新数据应能覆盖
        elif existing.client_updated_at is None or item.client_updated_at > existing.client_updated_at:
            # LWW：客户端时间戳更新才覆盖
            existing.item_type = item.item_type
            existing.data = json.dumps(item.data, ensure_ascii=False)
            existing.client_created_at = item.client_created_at
            existing.client_updated_at = item.client_updated_at
            updated += 1
            results.append(SyncPushResult(
                client_uuid=item.client_uuid,
                action="updated",
            ))
        else:
            # 客户端数据不比云端新，跳过
            skipped += 1
            results.append(SyncPushResult(
                client_uuid=item.client_uuid,
                action="skipped",
            ))

    db.commit()

    return SyncPushResponse(
        pushed=created + updated,
        created=created,
        updated=updated,
        skipped=skipped,
        results=results,
    )


@router.get("/pull", response_model=SyncPullResponse)
def sync_pull(
    app: str = Query(..., description="应用标识：focus / muse"),
    user_id: int = Depends(require_user),
    db: Session = Depends(get_db),
):
    """拉取云端数据到本地。

    返回该用户指定 app 的所有同步项。
    dev 模式（user_id=0）返回 admin 推送的数据。
    """
    if app not in ALLOWED_SYNC_APPS:
        raise HTTPException(status_code=422, detail=f"app 必须是 {sorted(ALLOWED_SYNC_APPS)} 之一")

    rows = (
        db.query(SyncItem)
        .filter(
            SyncItem.owner_id == user_id,
            SyncItem.app == app,
        )
        .all()
    )

    items = []
    for row in rows:
        try:
            data = json.loads(row.data) if row.data else {}
        except (json.JSONDecodeError, TypeError):
            logger.warning("sync_item %d 的 data 字段 JSON 解析失败，跳过", row.id)
            continue
        items.append(SyncItemOut(
            client_uuid=row.client_uuid,
            app=row.app,
            item_type=row.item_type,
            data=data,
            client_created_at=row.client_created_at,
            client_updated_at=row.client_updated_at,
        ))

    return SyncPullResponse(
        app=app,
        count=len(items),
        items=items,
    )
