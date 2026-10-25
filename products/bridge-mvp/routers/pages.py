# routers/pages.py — Bridge MVP HTML 页面
import base64
import importlib.util
import io
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import qrcode
from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session, selectinload

from auth import require_admin
from database import get_db
from models import Message, Project
from schemas import ALLOWED_PROJECT_STATUS
from templates import get_all_templates

logger = logging.getLogger(__name__)

router = APIRouter(tags=["pages"])
templates = Jinja2Templates(directory="templates")

# H-4: 使用固定 BASE_URL 避免 Host 头注入
# W088b: 增强防御——可信主机白名单 + 拒绝未配置时回退到 request.base_url
# 三层防御：
#   1. 优先使用 BRIDGE_BASE_URL 环境变量（固定值，完全规避 Host 头注入）
#   2. 未配置时校验请求 Host 是否在 BRIDGE_ALLOWED_HOSTS 白名单中
#   3. 白名单校验失败则回退到 127.0.0.1:8000（本地默认，安全兜底）
BASE_URL = os.getenv("BRIDGE_BASE_URL", "")
if BASE_URL and not BASE_URL.endswith("/"):
    BASE_URL += "/"

# W088b: 可信主机白名单，逗号分隔，如 "bridge.example.com,localhost,127.0.0.1"
# 未配置时仅允许 localhost / 127.0.0.1（开发环境安全默认）
_ALLOWED_HOSTS_RAW = os.getenv("BRIDGE_ALLOWED_HOSTS", "localhost,127.0.0.1")
ALLOWED_HOSTS = {h.strip() for h in _ALLOWED_HOSTS_RAW.split(",") if h.strip()}


def _is_host_trusted(host: str) -> bool:
    """校验请求 Host 是否在可信白名单中。

    防御 Host 头注入攻击：攻击者发送恶意 Host 头（如 evil.com），
    若服务直接信任 request.base_url 拼接 URL，会导致二维码链接指向攻击者域名。
    """
    if not host:
        return False
    # 去除端口部分（如 bridge.example.com:8080 → bridge.example.com）
    hostname = host.split(":")[0].lower()
    return hostname in ALLOWED_HOSTS


# L8: 统一的 404 错误信息
NOT_FOUND_MSG = "项目不存在或链接已失效"


def _share_url(token: str, request: Request | None = None) -> str:
    """拼接志愿者分享链接。

    W088b: 三层防御 Host 头注入：
        1. 优先使用 BRIDGE_BASE_URL 环境变量（固定值，完全规避）
        2. 未配置时校验 request.headers['host'] 是否在 BRIDGE_ALLOWED_HOSTS 白名单
        3. 白名单校验失败回退到 127.0.0.1:8000（安全兜底，不信任请求 Host）
    """
    if BASE_URL:
        return f"{BASE_URL}p/{token}"
    # 未配置 BASE_URL 时，校验请求 Host 是否可信
    if request is not None:
        req_host = request.headers.get("host", "")
        if _is_host_trusted(req_host):
            base = str(request.base_url)
            if not base.endswith("/"):
                base += "/"
            return f"{base}p/{token}"
    # 白名单校验失败或无 request：安全兜底
    return f"http://127.0.0.1:8000/p/{token}"


def _escape_like(q: str) -> str:
    """M5: 转义 LIKE 通配符 % 和 _，避免用户输入影响匹配语义。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _qr_data_uri(url: str) -> str:
    """L5: 生成二维码 data URI，失败时抛 HTTPException 由调用方处理。"""
    try:
        img = qrcode.make(url, box_size=6, border=2)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception as exc:
        logger.exception("二维码 data URI 生成失败")
        raise HTTPException(status_code=400, detail="二维码生成失败") from exc


# 项目状态徽章的中文标签映射，供模板使用
PROJECT_STATUS_LABELS = {
    "active": "进行中",
    "archived": "已归档",
    "done": "已完成",
}


@router.get("/", response_class=HTMLResponse)
def admin_index(
    request: Request,
    q: str = "",
    status: str = Query("", description="按状态筛选：active / archived / done；留空返回全部"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    query = db.query(Project)
    if q:
        like = f"%{_escape_like(q)}%"
        query = query.filter(
            Project.name.ilike(like, escape="\\")
            | Project.description.ilike(like, escape="\\")
        )
    if status:
        if status not in ALLOWED_PROJECT_STATUS:
            raise HTTPException(status_code=422, detail=f"status 必须是 {ALLOWED_PROJECT_STATUS} 之一")
        query = query.filter(Project.status == status)
    projects = query.order_by(Project.updated_at.desc()).offset(skip).limit(limit).all()
    ctx = {
        "projects": projects,
        "q": q,
        "status": status,
        "status_labels": PROJECT_STATUS_LABELS,
        "templates": get_all_templates(),
        "skip": skip,
        "limit": limit,
    }
    return templates.TemplateResponse(request, "index.html", ctx)


@router.get("/admin/stats", response_class=HTMLResponse)
def admin_stats(request: Request, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    """第三批升级：统计仪表盘页面。"""
    from routers.projects import compute_stats

    stats = compute_stats(db)
    ctx = {
        "stats": stats,
        "status_labels": PROJECT_STATUS_LABELS,
    }
    return templates.TemplateResponse(request, "stats.html", ctx)


@router.get("/admin/projects/{project_id}", response_class=HTMLResponse)
def admin_project_detail(request: Request, project_id: int, db: Session = Depends(get_db), admin: str = Depends(require_admin)):
    # M3: 使用 selectinload 预加载关系，消除 N+1 查询
    # P0: 预加载 message.task 让留言气泡展示关联任务标签
    project = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.handoffs),
            selectinload(Project.messages).selectinload(Message.task),
        )
        .filter(Project.id == project_id)
        .first()
    )
    if not project:
        # P1-DOG-01 修复：直接返回 TemplateResponse 渲染 error.html，
        # 而非 raise HTTPException（后者经中间件 call_next 会丢失 body）。
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "项目未找到", "message": NOT_FOUND_MSG, "code": 404},
            status_code=404,
        )
    share_url = _share_url(project.qr_token, request)
    ctx = {
        "project": project,
        "share_url": share_url,
        "status_labels": PROJECT_STATUS_LABELS,
        "templates": get_all_templates(),
        # 停滞提醒：传 naive UTC now，与 SQLite 读回的 naive datetime 兼容
        # B-P2-7: 必须保持 naive（不带 tzinfo），与 models.py 中 DateTime 列不对齐 timezone=True 配合。
        # 改 timezone=True 或此处带 tzinfo 会导致 (now - t.updated_at) TypeError。勿改。
        "now": datetime.now(timezone.utc).replace(tzinfo=None),
    }
    return templates.TemplateResponse(request, "project_detail.html", ctx)


@router.get("/p/{token}", response_class=HTMLResponse)
def volunteer_view(request: Request, token: str, db: Session = Depends(get_db)):
    # P0: 预加载 message.task 让志愿者页留言气泡展示关联任务标签
    project = (
        db.query(Project)
        .options(
            selectinload(Project.tasks),
            selectinload(Project.handoffs),
            selectinload(Project.messages).selectinload(Message.task),
        )
        .filter(Project.qr_token == token)
        .first()
    )
    if not project:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "链接已失效", "message": NOT_FOUND_MSG, "code": 404},
            status_code=404,
        )
    return templates.TemplateResponse(request, "volunteer.html", {"project": project})


@router.get("/qr/{token}", response_class=HTMLResponse)
def qr_page(request: Request, token: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.qr_token == token).first()
    if not project:
        return templates.TemplateResponse(
            request,
            "error.html",
            {"title": "链接已失效", "message": NOT_FOUND_MSG, "code": 404},
            status_code=404,
        )
    share_url = _share_url(token, request)
    qr_uri = _qr_data_uri(share_url)
    return templates.TemplateResponse(
        request,
        "qr.html",
        {"project": project, "share_url": share_url, "qr_uri": qr_uri},
    )


@router.get("/qr/{token}/image", response_class=Response)
def qr_image(request: Request, token: str, db: Session = Depends(get_db)):
    project = db.query(Project).filter(Project.qr_token == token).first()
    if not project:
        raise HTTPException(status_code=404, detail=NOT_FOUND_MSG)
    share_url = _share_url(token, request)
    # L5: 二维码生成异常处理
    try:
        img = qrcode.make(share_url, box_size=6, border=2)
        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return Response(content=buffer.getvalue(), media_type="image/png")
    except Exception as exc:
        logger.exception("二维码图片生成失败")
        raise HTTPException(status_code=400, detail="二维码生成失败") from exc


# ============================================================
# Phase 5.3: /demo 演示数据管理路由（仅 BRIDGE_DEBUG=1 可用）
# ============================================================

_seed_demo_module = None


def _get_seed_demo_module():
    """延迟加载 scripts/seed_demo_data.py（scripts 非 Python 包，用 importlib 加载）。

    模块级缓存避免每次请求重复加载。
    """
    global _seed_demo_module
    if _seed_demo_module is None:
        seed_path = Path(__file__).resolve().parent.parent / "scripts" / "seed_demo_data.py"
        spec = importlib.util.spec_from_file_location("seed_demo_data", seed_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _seed_demo_module = module
    return _seed_demo_module


def _require_debug_or_404():
    """BRIDGE_DEBUG=1 时通过，否则抛 404 隐藏演示入口（生产环境不可访问）。"""
    if os.getenv("BRIDGE_DEBUG") != "1":
        raise HTTPException(status_code=404, detail="Not Found")


@router.get("/demo", response_class=HTMLResponse)
def demo_page(
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Phase 5.3: 演示数据集管理页（仅 BRIDGE_DEBUG=1 可用）。

    展示当前数据状态 + 加载/重置按钮 + 跳转到 /admin/stats 仪表盘。
    """
    _require_debug_or_404()

    # 延迟导入避免顶层循环依赖
    from models import (
        AgentSuggestion,
        Handoff,
        ProjectContextInspiration,
        ProjectContextSnapshot,
        SyncItem,
        Task,
        VannevarMeta,
    )

    demo_seeded = db.query(VannevarMeta).filter_by(key="demo_seeded").first()
    is_seeded = demo_seeded is not None and demo_seeded.value == "true"

    counts = {
        "projects": db.query(Project).count(),
        "tasks": db.query(Task).count(),
        "messages": db.query(Message).count(),
        "handoffs": db.query(Handoff).count(),
        "snapshots": db.query(ProjectContextSnapshot).count(),
        "inspirations": db.query(ProjectContextInspiration).count(),
        "suggestions": db.query(AgentSuggestion).count(),
        "sync_items": db.query(SyncItem).count(),
    }

    ctx = {
        "is_seeded": is_seeded,
        "counts": counts,
    }
    return templates.TemplateResponse(request, "demo.html", ctx)


@router.post("/demo/load")
def demo_load(
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Phase 5.3: 播种演示数据（幂等：已播种则跳过）。

    Returns:
        {"seeded": True, "counts": {...}} — 新播种
        {"skipped": True, "reason": str}   — 已播种跳过
    """
    _require_debug_or_404()

    seed_mod = _get_seed_demo_module()
    result = seed_mod.seed_demo_data(db, force=False)
    logger.info("AUDIT actor=%s demo_load result=%s", admin, result)
    return result


@router.post("/demo/reset")
def demo_reset(
    payload: dict = Body(default={}),
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Phase 5.3: 清空演示数据（按依赖顺序删除子表→父表）。

    R1b P2-4 修复：服务端强制校验 payload.confirm=True，防 CSRF 误触发。
    前端 demo.html showModal onConfirm 已传递 confirm=True。
    """
    _require_debug_or_404()

    # R1b P2-4: 服务端 confirm 校验，curl/Postman 直接调用也必须显式传 confirm=True
    if not payload.get("confirm"):
        raise HTTPException(
            status_code=400,
            detail={"code": "confirm_required", "message": "需在请求体中传 {\"confirm\": true} 确认清空"},
        )

    seed_mod = _get_seed_demo_module()
    result = seed_mod.reset_demo_data(db)
    logger.info("AUDIT actor=%s demo_reset result=%s", admin, result)
    return result


# ============================================================
# Phase 6.L: 故事模式 + 时间轴回放
# ============================================================

# 章节定义：将 30 天回放切分为 5 个章节，每章聚焦一个产品价值点
# chapter_key -> (start_frame_idx, title, narrative_template)
# 帧索引从 0（D-29，最早）到 29（D-0，今天）
_STORY_CHAPTERS = [
    {
        "key": "kickoff",
        "start_frame": 0,
        "title": "开局 · 项目档案建立",
        "narrative": "Bridge 之旅起步：第一个项目档案创建，志愿者通过二维码加入，任务看板从零到一。展示 Bridge 如何把零散的公益行动沉淀为可追溯的项目档案。",
    },
    {
        "key": "collaboration",
        "start_frame": 6,
        "title": "协作 · 跨角色任务流",
        "narrative": "任务在志愿者间接力，留言讨论方案，交接记录保证责任不丢。这一段展示了 Bridge 的核心：让交接不再依赖微信群截图，让每个任务都有来龙去脉。",
    },
    {
        "key": "agent",
        "start_frame": 12,
        "title": "智能 · Agent 主动建议",
        "narrative": "Agent 开始介入：识别停滞任务、超期任务、交接缺口，主动推送建议。用户可以看到 AI 不是噱头，而是真正分担了项目协调的脑力劳动。",
    },
    {
        "key": "context",
        "start_frame": 18,
        "title": "上下文 · Focus 快照与 Muse 灵感",
        "narrative": "Focus 扩展主动推送标签上下文快照，Muse 捕捉灵感卡片。Bridge 不止记录过去，还帮助志愿者把当下的认知和灵感沉淀进项目档案。",
    },
    {
        "key": "review",
        "start_frame": 24,
        "title": "复盘 · 仪表盘与跨项目洞察",
        "narrative": "30 天回放到终点：跨项目桑基图揭示志愿者流向，雷达图展示项目健康度，网络图呈现协作生态。用户可以一屏看清整个公益生态的脉搏。",
    },
]


def _build_story_frames(activity_heatmap: list) -> dict:
    """Phase 6.L: 把 compute_stats 的 activity_heatmap 30 天数据转换为故事模式帧。

    每帧附加：
    - cumulative: 从第 0 帧累计到当前帧的总和（5 维）
    - chapter: 该帧所属的章节 key（用于章节高亮）
    - day_label: "Day N"（N = 1..30，1=最早，30=今天）

    Returns:
        {
            "frames": [...],          # 30 帧，含 cumulative + chapter + day_label
            "chapters": [...],        # 5 章节，含 start_frame + end_frame
            "total_frames": 30,
            "max_daily_total": int,   # 单日峰值（用于帧条形图缩放）
            "max_cumulative": int,    # 累计峰值（用于进度条缩放）
        }
    """
    n = len(activity_heatmap)
    if n == 0:
        return {
            "frames": [],
            "chapters": [],
            "total_frames": 0,
            "max_daily_total": 1,
            "max_cumulative": 1,
        }

    # 计算累计值
    cum = {"projects": 0, "tasks": 0, "messages": 0, "snapshots": 0, "inspirations": 0, "total": 0}
    frames = []
    for idx, day in enumerate(activity_heatmap):
        for k in cum:
            cum[k] += day.get(k, 0)
        # 章节归属：找到 start_frame <= idx 的最后一个章节
        chapter_key = _STORY_CHAPTERS[0]["key"]
        for ch in _STORY_CHAPTERS:
            if idx >= ch["start_frame"]:
                chapter_key = ch["key"]
            else:
                break
        frames.append({
            "idx": idx,
            "date": day.get("date", ""),
            "day_label": f"Day {idx + 1}",
            "delta": {
                "projects": day.get("projects", 0),
                "tasks": day.get("tasks", 0),
                "messages": day.get("messages", 0),
                "snapshots": day.get("snapshots", 0),
                "inspirations": day.get("inspirations", 0),
                "total": day.get("total", 0),
            },
            "cumulative": dict(cum),
            "chapter": chapter_key,
        })

    # 章节起止帧
    chapters = []
    for i, ch in enumerate(_STORY_CHAPTERS):
        start = ch["start_frame"]
        end = (_STORY_CHAPTERS[i + 1]["start_frame"] - 1) if i + 1 < len(_STORY_CHAPTERS) else n - 1
        chapters.append({
            "key": ch["key"],
            "title": ch["title"],
            "narrative": ch["narrative"],
            "start_frame": start,
            "end_frame": end,
        })

    max_daily = max((f["delta"]["total"] for f in frames), default=1) or 1
    max_cum = max((f["cumulative"]["total"] for f in frames), default=1) or 1

    return {
        "frames": frames,
        "chapters": chapters,
        "total_frames": n,
        "max_daily_total": max_daily,
        "max_cumulative": max_cum,
    }


@router.get("/demo/story", response_class=HTMLResponse)
def demo_story(
    request: Request,
    db: Session = Depends(get_db),
    admin: str = Depends(require_admin),
):
    """Phase 6.L: 故事模式 + 时间轴回放页（仅 BRIDGE_DEBUG=1 可用）。

    复用 compute_stats 的 activity_heatmap 30 天数据作为回放帧，前端 JS 控制播放/暂停/步进。
    章节导航将 30 帧切分为 5 个产品价值点章节，用户可快速跳转。
    """
    _require_debug_or_404()

    from routers.projects import compute_stats

    stats = compute_stats(db)
    story = _build_story_frames(stats.get("activity_heatmap", []))

    ctx = {
        "story": story,
        "stats": stats,
        "status_labels": PROJECT_STATUS_LABELS,
    }
    return templates.TemplateResponse(request, "story.html", ctx)
