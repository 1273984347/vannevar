"""scripts/seed_demo_data.py — Phase 5 演示数据集生成脚本

================================================================
Phase 5.1 演示数据集 Schema 设计
================================================================

目标：用户首次进入 /admin/stats 即看到完整看板效果，
      覆盖 5 条折线图 + 30 天热力图 5 级色阶 + Agent 建议环形图 4 种 type。

数据规模：
- 3 个项目（不同 status）
    P1 "周末探访老人院"     (active, volunteer, created D29)
    P2 "山区图书角募捐"     (active, volunteer, created D20)
    P3 "社区无偿法律援助"   (done,   volunteer, created D12, updated D0)

- 24 个任务（每个项目 8 个，分布到 todo/in_progress/done）
    P1: 2 todo + 3 in_progress + 3 done
    P2: 3 todo + 2 in_progress + 3 done
    P3: 0 todo + 0 in_progress + 8 done

- 15 条留言（含 1 条 moderation_flagged + 1 条 is_deleted）
    P1: 6 条 / P2: 4 条 / P3: 5 条

- 3 份交接摘要
    P1: status=draft
    P2: status=confirmed (2 天前)
    P3: status=confirmed (20 天前)

- 10 ProjectContextSnapshot（Focus 快照，ms 时间戳）
- 10 ProjectContextInspiration（Muse 灵感，ms 时间戳）
- 8 AgentSuggestion（4 种 type × 2 种 status）

时间戳分布（D0=today, D29=30 天前）：

    Day  | Proj | Task | Msg | Snap | Insp | Total | Level
    D0   |  0   |  3   |  2  |  1   |  1   |   7   | h-3
    D1   |  0   |  1   |  1  |  1   |  1   |   4   | h-2
    D2   |  0   |  2   |  2  |  1   |  1   |   6   | h-2
    D3   |  0   |  1   |  0  |  1   |  1   |   3   | h-1
    D4   |  0   |  2   |  0  |  1   |  1   |   4   | h-2
    D5   |  0   |  1   |  1  |  1   |  1   |   4   | h-2
    D6   |  0   |  1   |  1  |  1   |  1   |   4   | h-2
    D7   |  0   |  2   |  1  |  1   |  0   |   4   | h-2
    D8   |  0   |  0   |  1  |  0   |  1   |   2   | h-1
    D9   |  0   |  1   |  0  |  0   |  0   |   1   | h-1
    D10  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D11  |  0   |  1   |  0  |  0   |  0   |   1   | h-1
    D12  |  1   |  4   |  2  |  2   |  2   |  11   | h-4
    D13  |  0   |  1   |  0  |  0   |  0   |   1   | h-1
    D14  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D15  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D16  |  0   |  1   |  0  |  0   |  0   |   1   | h-1
    D17  |  0   |  1   |  1  |  0   |  0   |   2   | h-1
    D18  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D19  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D20  |  1   |  0   |  0  |  0   |  0   |   1   | h-1
    D21  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D22  |  0   |  0   |  0  |  0   |  0   |   0   | h-0
    D23-D28 | 0 | 0   |  0  |  0   |  0   |   0   | h-0
    D29  |  1   |  0   |  0  |  0   |  0   |   1   | h-1

5 级色阶覆盖：
    h-0: 13 天 / h-1: 9 天 / h-2: 6 天 / h-3: 1 天 / h-4: 1 天 = 30 天 ✓

近 7 天折线图（D0-D6）：
    daily_projects:    D0=0, D1-D6=0（项目创建稀疏，line 在 0 处）
    daily_tasks:       D0-D6 每天非零 ✓
    daily_messages:    D0-D6 每天非零（D3/D4 除外，D3/D4 用其他源补齐）
    daily_snapshots:   D0-D6 每天非零 ✓
    daily_inspirations: D0-D6 每天非零 ✓

Environment Variables (R1a-C #1):
    BRIDGE_DEBUG=1           # 必需：/demo 路由仅在此模式下可用（生产返回 404）
    BRIDGE_ADMIN_PASS        # 非 dev 模式下必需，否则启动时 RuntimeError（auth.py L48-52）
    BRIDGE_JWT_SECRET        # 非 dev 模式下必需（auth.py _get_jwt_secret）
    BRIDGE_DATABASE_URL      # 数据库连接串，默认 sqlite:///bridge.db

Usage:
    python scripts/seed_demo_data.py            # 播种演示数据（幂等：已播种则跳过）
    python scripts/seed_demo_data.py --reset    # 清空后重新播种（等同 --force）
    python scripts/seed_demo_data.py --force    # 强制重新播种（等同 --reset）

    注（R1a-C #4）：--reset 与 --force 行为完全等价（均调用 reset_demo_data + seed_demo_data），
    保留两个选项仅为语义友好；argparse help 文本已同步标注。
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# 添加父目录到 sys.path 以便导入 bridge_mvp 模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy.orm import Session  # noqa: E402

from database import SessionLocal  # noqa: E402
from models import (  # noqa: E402
    AgentSuggestion,
    Handoff,
    Message,
    OrgInvitation,
    OrgMembership,
    Organization,
    Project,
    ProjectContextInspiration,
    ProjectContextSnapshot,
    ProjectShare,
    SyncItem,
    Task,
    Team,
    TeamMembership,
    User,
    VannevarMeta,
)

logger = logging.getLogger(__name__)

DEMO_META_KEY = "demo_seeded"


# ============================================================
# 时间工具
# ============================================================

def _utcnow_naive() -> datetime:
    """返回 naive UTC datetime（与 SQLite DateTime 列对齐，避免时区减法失败）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _day_offset(days_ago: int, hour: int = 10, minute: int = 0) -> datetime:
    """返回 (days_ago) 天前的 naive UTC datetime（默认当天 10:00 UTC）。

    用于 ORM DateTime 列（Project/Task/Message/Handoff/AgentSuggestion.created_at）。
    """
    base = _utcnow_naive()
    return base - timedelta(days=days_ago, hours=base.hour - hour, minutes=base.minute - minute)


def _day_offset_ms(days_ago: int, hour: int = 10, minute: int = 0) -> int:
    """返回 (days_ago) 天前的 ms 时间戳（Integer）。

    用于 Phase 4.1 表（ProjectContextSnapshot/Inspiration.created_at/pushed_at）。
    """
    dt = _day_offset(days_ago, hour, minute)
    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1000)


# ============================================================
# 数据定义
# ============================================================

def _build_projects() -> list[dict]:
    """3 个项目（不同 status + 创建时间分布）。

    Phase 6.1 叙事扩展：3 个项目归入「阳光街道社区 2025 年度公益计划」季度接力。
        Q1（D29-D20）：P1 周末探访老人院启动
        Q2（D20-D12）：P2 山区图书角募捐接力（P1 物资经验传递）
        Q3（D12-D0）：P3 社区无偿法律援助接力（小李从 P3 学习后转入 P1）

    分布：
        P1 (active): D29 创建（最老，长期运行）
        P2 (active): D20 创建
        P3 (done):   D12 创建，updated_at=D0（刚完成）
    """
    return [
        {
            "name": "周末探访老人院",
            "description": "每周六下午探访社区老人院，陪伴聊天、读报、简单文娱活动，让老人感受到社会的关怀与温暖。由阳光街道社区公益基金支持。",
            "status": "active",
            "template_id": "volunteer",
            "created_at": _day_offset(29, hour=9),
            "updated_at": _day_offset(0, hour=9),
        },
        {
            "name": "山区图书角募捐",
            "description": "为偏远山区小学募集图书并建立图书角，让山里的孩子也能读到课外读物，打开一扇看世界的窗。由阳光街道社区公益基金支持。",
            "status": "active",
            "template_id": "volunteer",
            "created_at": _day_offset(20, hour=11),
            "updated_at": _day_offset(0, hour=11),
        },
        {
            "name": "社区无偿法律援助",
            "description": "为社区经济困难居民提供免费法律咨询与代书服务，已完成 8 个咨询案例并归档。由阳光街道社区公益基金支持。",
            "status": "done",
            "template_id": "volunteer",
            "created_at": _day_offset(12, hour=14),
            "updated_at": _day_offset(0, hour=14),
        },
    ]


def _build_tasks(project_ids: list[int]) -> list[dict]:
    """24 个任务分布到 3 个项目（每项目 8 个）。

    P1: 2 todo + 3 in_progress + 3 done
    P2: 3 todo + 2 in_progress + 3 done
    P3: 0 todo + 0 in_progress + 8 done
    """
    p1, p2, p3 = project_ids
    return [
        # ---- P1 周末探访老人院（8 任务：2 todo + 3 in_progress + 3 done）----
        {"project_id": p1, "title": "项目启动会议纪要", "status": "done", "owner_name": "小李",
         "description": "首次探访志愿者会议纪要，明确分工与排期。", "created_at": _day_offset(29, hour=10)},
        {"project_id": p1, "title": "周六下午探访执行", "status": "in_progress", "owner_name": "小王",
         "description": "本周六 14:00-17:00 探访执行，含读报、聊天、文娱三个环节。", "created_at": _day_offset(17, hour=13)},
        {"project_id": p1, "title": "志愿者招募启事", "status": "done", "owner_name": "小李",
         "description": "撰写并发布招募启事，目标 5 人。", "created_at": _day_offset(12, hour=10)},
        {"project_id": p1, "title": "活动照片整理归档", "status": "done", "owner_name": "小张",
         "description": "上周活动照片上传云盘并按日期归档。", "created_at": _day_offset(12, hour=15)},
        {"project_id": p1, "title": "本周读报素材准备", "status": "todo", "owner_name": "小李",
         "description": "整理本周读报主题：科技与生活，准备 5 篇素材。", "created_at": _day_offset(6, hour=9)},
        {"project_id": p1, "title": "老人兴趣档案建立", "status": "in_progress", "owner_name": "小王",
         "description": "为每位老人建立兴趣档案，记录偏好便于精准陪伴。", "created_at": _day_offset(4, hour=11)},
        {"project_id": p1, "title": "探访物资采购清单", "status": "todo", "owner_name": "小张",
         "description": "水果、报纸、文娱道具采购清单与预算。", "created_at": _day_offset(2, hour=14)},
        {"project_id": p1, "title": "周末探访总结", "status": "in_progress", "owner_name": "小王",
         "description": "本周末探访活动总结与下周计划。", "created_at": _day_offset(0, hour=16)},

        # ---- P2 山区图书角募捐（8 任务：3 todo + 2 in_progress + 3 done）----
        {"project_id": p2, "title": "联系出版社捐赠", "status": "in_progress", "owner_name": "小赵",
         "description": "联系 3 家出版社争取图书捐赠，已获 1 家承诺 200 本童书。", "created_at": _day_offset(20, hour=10)},
        {"project_id": p2, "title": "整理图书清单", "status": "done", "owner_name": "小赵",
         "description": "已捐图书按年龄段分类整理。", "created_at": _day_offset(16, hour=15)},
        {"project_id": p2, "title": "图书角选址确认", "status": "done", "owner_name": "小赵",
         "description": "已确认 3 所山区小学图书角位置。", "created_at": _day_offset(13, hour=11)},
        {"project_id": p2, "title": "募捐启事发布", "status": "done", "owner_name": "小钱",
         "description": "募捐启事已发布到社区群与朋友圈。", "created_at": _day_offset(12, hour=14)},
        {"project_id": p2, "title": "捐赠者答谢卡制作", "status": "todo", "owner_name": "小孙",
         "description": "为每位捐赠者制作手写答谢卡。", "created_at": _day_offset(11, hour=10)},
        {"project_id": p2, "title": "图书分类标签制作", "status": "in_progress", "owner_name": "小孙",
         "description": "图书分类标签设计稿已出，待印制。", "created_at": _day_offset(9, hour=13)},
        {"project_id": p2, "title": "物流配送安排", "status": "todo", "owner_name": "小张",
         "description": "联系物流公司配送图书到山区小学（小张跨项目协调：P1 探访物资 + P2 图书物流）。", "created_at": _day_offset(7, hour=9)},
        {"project_id": p2, "title": "下批图书清单更新", "status": "todo", "owner_name": "小钱",
         "description": "更新下批捐赠图书清单，目标再筹 300 本。", "created_at": _day_offset(0, hour=15)},

        # ---- P3 社区无偿法律援助（8 任务：0 todo + 0 in_progress + 8 done）----
        # Phase 6.1 叙事扩展：任务时间反向重排（项目启动 D12 → 结案总结 D0，时间正序）
        # 跨项目志愿者：小李负责 P3 登记表设计后转入 P1（法律视角优化老人权益保护）
        {"project_id": p3, "title": "项目启动", "status": "done", "owner_name": "小周",
         "description": "项目启动会议，明确服务对象与流程。", "created_at": _day_offset(12, hour=14)},
        {"project_id": p3, "title": "法律咨询登记表设计", "status": "done", "owner_name": "小李",
         "description": "登记表字段：姓名、联系方式、咨询事项、紧急程度。含隐私保护条款。", "created_at": _day_offset(11, hour=13)},
        {"project_id": p3, "title": "首场法律咨询活动", "status": "done", "owner_name": "小周",
         "description": "首场咨询活动服务 12 位居民，反馈良好。", "created_at": _day_offset(10, hour=9)},
        {"project_id": p3, "title": "咨询案例归档", "status": "done", "owner_name": "小吴",
         "description": "8 个咨询案例卷宗归档，含咨询记录与法律意见书。", "created_at": _day_offset(8, hour=16)},
        {"project_id": p3, "title": "法律知识宣传册编写", "status": "done", "owner_name": "小吴",
         "description": "编写婚姻家庭、劳动权益、房产纠纷三章宣传册。", "created_at": _day_offset(6, hour=11)},
        {"project_id": p3, "title": "宣传册印制发放", "status": "done", "owner_name": "小周",
         "description": "宣传册 500 份印制完成并发放到社区。", "created_at": _day_offset(4, hour=14)},
        {"project_id": p3, "title": "活动效果评估报告", "status": "done", "owner_name": "小吴",
         "description": "8 个咨询案例满意度调研与效果评估。", "created_at": _day_offset(2, hour=10)},
        {"project_id": p3, "title": "项目结案总结", "status": "done", "owner_name": "小周",
         "description": "项目结案报告已提交资助方。", "created_at": _day_offset(0, hour=15)},
    ]


def _build_messages(project_ids: list[int], task_id_map: dict) -> list[dict]:
    """15 条留言（含 1 条 moderation_flagged + 1 条 is_deleted）。

    Phase 6.1 叙事扩展：P3 补筹备期留言 + P1 补启动会议纪要 + 留言时间与新任务对齐。
    分布：P1=6 条 / P2=4 条 / P3=5 条
    """
    p1, p2, p3 = project_ids
    return [
        # ---- P1 (6 条，含 1 moderation_flagged + 1 is_deleted) ----
        # Phase 6.1 新增：项目启动会议纪要留言（D29，与 task D29 对齐）
        {"project_id": p1, "task_id": task_id_map.get((p1, "项目启动会议纪要")),
         "author": "小李", "content": "项目启动会议纪要已整理，请查阅分工与排期。",
         "created_at": _day_offset(29, hour=11), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p1, "task_id": None, "author": "小李", "content": "本周探访安排已更新，请大家查看任务列表。",
         "created_at": _day_offset(0, hour=9), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p1, "task_id": task_id_map.get((p1, "本周读报素材准备")),
         "author": "小王", "content": "周六我负责带读报，已准备好 5 篇素材。",
         "created_at": _day_offset(0, hour=10), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p1, "task_id": task_id_map.get((p1, "探访物资采购清单")),
         "author": "小张", "content": "物资采购清单已发邮件，请查收。预算约 200 元。",
         "created_at": _day_offset(2, hour=14), "moderation_flagged": False, "is_deleted": False},
        # moderation_flagged：含敏感词触发审核标记（仅标记，不阻止提交）
        {"project_id": p1, "task_id": task_id_map.get((p1, "活动照片整理归档")),
         "author": "小李", "content": "上周活动照片已上传云盘，含敏感人物需审核后再公开。",
         "created_at": _day_offset(17, hour=16), "moderation_flagged": True,
         "moderation_warning": "留言中包含敏感词，已标记审核。审核通过前不会在志愿者视图显示。"},
        # is_deleted：管理员软删除（志愿者视图不显示，管理员视图标记「已删除」）
        {"project_id": p1, "task_id": None, "author": "小王", "content": "本周读报素材主题：科技与生活（已删除）",
         "created_at": _day_offset(12, hour=11), "moderation_flagged": False, "is_deleted": True},

        # ---- P2 (4 条) ----
        {"project_id": p2, "task_id": None, "author": "小赵", "content": "出版社反馈：可捐赠 200 本童书，下周到货。",
         "created_at": _day_offset(1, hour=10), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p2, "task_id": task_id_map.get((p2, "物流配送安排")),
         "author": "小张", "content": "物流公司报价已收到 3 家，对比后选最便宜的。小张跨项目对接 P1+P2 物资。",
         "created_at": _day_offset(5, hour=14), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p2, "task_id": task_id_map.get((p2, "图书分类标签制作")),
         "author": "小孙", "content": "图书分类标签设计稿已出，含 6 个大类 18 个小类。",
         "created_at": _day_offset(8, hour=11), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p2, "task_id": task_id_map.get((p2, "募捐启事发布")),
         "author": "小赵", "content": "募捐启事已发布到社区群与朋友圈，已有 5 人私下询问。",
         "created_at": _day_offset(12, hour=15), "moderation_flagged": False, "is_deleted": False},

        # ---- P3 (5 条，Phase 6.1 新增 2 条筹备期留言) ----
        # Phase 6.1 新增：筹备期留言（D11，与 task D11 登记表设计对齐）
        {"project_id": p3, "task_id": task_id_map.get((p3, "法律咨询登记表设计")),
         "author": "小李", "content": "登记表字段设计完成，含隐私保护条款，请律师志愿者审核。",
         "created_at": _day_offset(11, hour=15), "moderation_flagged": False, "is_deleted": False},
        # Phase 6.1 新增：筹备期留言（D8，首场咨询前物资准备）
        {"project_id": p3, "task_id": None, "author": "小周", "content": "首场咨询活动物资准备就绪，登记表 50 份已印制。",
         "created_at": _day_offset(8, hour=10), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p3, "task_id": task_id_map.get((p3, "首场法律咨询活动")),
         "author": "小周", "content": "首场法律咨询定于本周六上午 9 点社区活动中心。",
         "created_at": _day_offset(9, hour=9), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p3, "task_id": task_id_map.get((p3, "法律知识宣传册编写")),
         "author": "小吴", "content": "宣传册内容审核完成，律师志愿者已校对。",
         "created_at": _day_offset(6, hour=15), "moderation_flagged": False, "is_deleted": False},
        {"project_id": p3, "task_id": task_id_map.get((p3, "项目结案总结")),
         "author": "小周", "content": "项目结案报告已提交资助方，感谢各位志愿者付出。",
         "created_at": _day_offset(1, hour=16), "moderation_flagged": False, "is_deleted": False},
    ]


def _build_handoffs(project_ids: list[int]) -> list[dict]:
    """3 份交接摘要（每项目 1 份）。"""
    p1, p2, p3 = project_ids
    now = _utcnow_naive()
    return [
        # P1: draft
        {
            "project_id": p1,
            "summary": (
                f"【自动生成于 {now.strftime('%Y-%m-%d %H:%M')}】\n\n"
                "未完成任务（5）：\n"
                "- [待办] 本周读报素材准备（小李）\n"
                "- [进行中] 周六下午探访执行（小王）\n"
                "- [进行中] 老人兴趣档案建立（小王）\n"
                "- [待办] 探访物资采购清单（小张）\n"
                "- [进行中] 周末探访总结（小王）\n\n"
                "任务进度：3/8 已完成"
            ),
            "outgoing_volunteer": "小王",
            "incoming_volunteer": "小李",
            "status": "draft",
            "created_at": _day_offset(0, hour=17),
        },
        # P2: confirmed (2 天前)
        {
            "project_id": p2,
            "summary": (
                f"【自动生成于 {(_day_offset(2, hour=10)).strftime('%Y-%m-%d %H:%M')}】\n\n"
                "未完成任务（5）：\n"
                "- [待办] 捐赠者答谢卡制作（小孙）\n"
                "- [进行中] 图书分类标签制作（小孙）\n"
                "- [待办] 物流配送安排（小钱）\n"
                "- [进行中] 联系出版社捐赠（小赵）\n"
                "- [待办] 下批图书清单更新（小钱）\n\n"
                "任务进度：3/8 已完成"
            ),
            "outgoing_volunteer": "小赵",
            "incoming_volunteer": "小钱",
            "status": "confirmed",
            "confirmed_at": _day_offset(2, hour=10),
            "confirmed_by": "小张",
            "created_at": _day_offset(2, hour=9),
        },
        # P3: confirmed (20 天前)
        # Phase 6.1 叙事扩展：incoming_volunteer 从 "—" 改为 "小李"
        # 小李从 P3 学习法律援助经验后转入 P1（用法律视角优化老人权益保护）
        {
            "project_id": p3,
            "summary": (
                f"【自动生成于 {(_day_offset(20, hour=14)).strftime('%Y-%m-%d %H:%M')}】\n\n"
                "项目已完结：8 个咨询案例全部归档，宣传册 500 份发放完毕。\n"
                "感谢律师志愿者小周、小吴的辛勤付出。\n"
                "小李在 P3 完成登记表设计后转入 P1，将法律视角带入老人探访项目。\n\n"
                "任务进度：8/8 已完成"
            ),
            "outgoing_volunteer": "小周",
            "incoming_volunteer": "小李",
            "status": "confirmed",
            "confirmed_at": _day_offset(20, hour=14),
            "confirmed_by": "小周",
            "created_at": _day_offset(20, hour=13),
        },
    ]


def _build_snapshots(project_ids: list[int]) -> list[dict]:
    """10 个 Focus 快照（ProjectContextSnapshot，ms 时间戳）。

    分布到 8 天（D0-D7 各 1 个 + D12 2 个），每条快照含 title/summary/urls_json/tab_count。
    """
    p1, p2, p3 = project_ids
    return [
        {"project_id": p1, "snapshot_uuid": str(uuid.uuid4()),
         "title": "周末探访活动标签页汇总",
         "summary": "探访活动相关 5 个标签页：老人院官网、活动方案、物资清单、志愿者通讯录、往期照片。",
         "urls_json": json.dumps(["https://example.com/elderly-home", "https://example.com/activity-plan"]),
         "tab_count": 5, "created_at": _day_offset_ms(0, hour=9), "pushed_at": _day_offset_ms(0, hour=9), "pushed_by": 0},
        {"project_id": p1, "snapshot_uuid": str(uuid.uuid4()),
         "title": "读报素材收集",
         "summary": "本周读报素材 8 个标签页：科技新闻、健康生活、历史故事、时事评论等。",
         "urls_json": json.dumps(["https://example.com/news-tech", "https://example.com/news-health"]),
         "tab_count": 8, "created_at": _day_offset_ms(1, hour=10), "pushed_at": _day_offset_ms(1, hour=10), "pushed_by": 0},
        {"project_id": p1, "snapshot_uuid": str(uuid.uuid4()),
         "title": "探访物资采购参考",
         "summary": "物资采购参考 3 个标签页：水果报价、文娱道具、预算模板。",
         "urls_json": json.dumps(["https://example.com/fruit-price"]),
         "tab_count": 3, "created_at": _day_offset_ms(2, hour=14), "pushed_at": _day_offset_ms(2, hour=14), "pushed_by": 0},
        {"project_id": p2, "snapshot_uuid": str(uuid.uuid4()),
         "title": "童书捐赠出版社调研",
         "summary": "出版社调研 12 个标签页：各大出版社童书目录、捐赠政策、联系方式。",
         "urls_json": json.dumps(["https://example.com/publisher-a", "https://example.com/publisher-b"]),
         "tab_count": 12, "created_at": _day_offset_ms(3, hour=11), "pushed_at": _day_offset_ms(3, hour=11), "pushed_by": 0},
        {"project_id": p2, "snapshot_uuid": str(uuid.uuid4()),
         "title": "图书角选址对比",
         "summary": "山区小学选址对比 6 个标签页：3 所小学的师资、学生数、交通情况。",
         "urls_json": json.dumps(["https://example.com/school-a"]),
         "tab_count": 6, "created_at": _day_offset_ms(4, hour=15), "pushed_at": _day_offset_ms(4, hour=15), "pushed_by": 0},
        {"project_id": p3, "snapshot_uuid": str(uuid.uuid4()),
         "title": "法律咨询案例参考",
         "summary": "法律咨询案例参考 4 个标签页：婚姻家庭、劳动权益、房产纠纷、法律援助流程。",
         "urls_json": json.dumps(["https://example.com/legal-case"]),
         "tab_count": 4, "created_at": _day_offset_ms(5, hour=10), "pushed_at": _day_offset_ms(5, hour=10), "pushed_by": 0},
        {"project_id": p1, "snapshot_uuid": str(uuid.uuid4()),
         "title": "本周读报主题素材",
         "summary": "本周读报主题：科技与生活 10 个标签页：AI、太空、生物科技、生活科技等。",
         "urls_json": json.dumps(["https://example.com/ai-news", "https://example.com/space-news"]),
         "tab_count": 10, "created_at": _day_offset_ms(6, hour=9), "pushed_at": _day_offset_ms(6, hour=9), "pushed_by": 0},
        {"project_id": p3, "snapshot_uuid": str(uuid.uuid4()),
         "title": "法律咨询活动流程参考",
         "summary": "活动流程参考 7 个标签页：登记表、流程图、律师对接、场地布置、宣传海报。",
         "urls_json": json.dumps(["https://example.com/legal-flow"]),
         "tab_count": 7, "created_at": _day_offset_ms(7, hour=14), "pushed_at": _day_offset_ms(7, hour=14), "pushed_by": 0},
        {"project_id": p1, "snapshot_uuid": str(uuid.uuid4()),
         "title": "志愿者招募启事参考",
         "summary": "招募启事参考 5 个标签页：招募文案模板、往期招募启事、志愿者反馈。",
         "urls_json": json.dumps(["https://example.com/recruit-tmpl"]),
         "tab_count": 5, "created_at": _day_offset_ms(12, hour=10), "pushed_at": _day_offset_ms(12, hour=10), "pushed_by": 0},
        {"project_id": p2, "snapshot_uuid": str(uuid.uuid4()),
         "title": "募捐启事文案参考",
         "summary": "募捐启事参考 8 个标签页：公益募捐文案、往期案例、社交媒体传播策略。",
         "urls_json": json.dumps(["https://example.com/donation-tmpl"]),
         "tab_count": 8, "created_at": _day_offset_ms(12, hour=15), "pushed_at": _day_offset_ms(12, hour=15), "pushed_by": 0},
    ]


def _build_inspirations(project_ids: list[int]) -> list[dict]:
    """10 个 Muse 灵感（ProjectContextInspiration，ms 时间戳）。

    分布到 8 天（D0-D6 各 1 个 + D8 1 个 + D12 2 个）。
    """
    p1, p2, p3 = project_ids
    return [
        {"project_id": p1, "inspiration_uuid": str(uuid.uuid4()),
         "title": "老年人陪伴沟通技巧文章",
         "url": "https://example.com/elderly-care-communication",
         "note": "文章提到「倾听比说更重要」，建议探访时多问开放性问题。",
         "mood": "温暖", "created_at": _day_offset_ms(0, hour=11), "pushed_at": _day_offset_ms(0, hour=11), "pushed_by": 0},
        {"project_id": p2, "inspiration_uuid": str(uuid.uuid4()),
         "title": "童书捐赠公益案例",
         "url": "https://example.com/children-books-donation",
         "note": "上海某社区一年募集 5000 本童书的运营经验，值得借鉴。",
         "mood": "启发", "created_at": _day_offset_ms(1, hour=12), "pushed_at": _day_offset_ms(1, hour=12), "pushed_by": 0},
        {"project_id": p3, "inspiration_uuid": str(uuid.uuid4()),
         "title": "法律援助志愿者心得",
         "url": "https://example.com/legal-aid-volunteer",
         "note": "一位法律援助志愿者 3 年的心得，提到「每位来访者都是一个故事」。",
         "mood": "感动", "created_at": _day_offset_ms(2, hour=15), "pushed_at": _day_offset_ms(2, hour=15), "pushed_by": 0},
        {"project_id": p1, "inspiration_uuid": str(uuid.uuid4()),
         "title": "读报素材选择指南",
         "url": "https://example.com/news-reading-guide",
         "note": "为老人读报的素材选择指南：避免负面新闻，多选积极向上的故事。",
         "mood": "实用", "created_at": _day_offset_ms(3, hour=10), "pushed_at": _day_offset_ms(3, hour=10), "pushed_by": 0},
        {"project_id": p2, "inspiration_uuid": str(uuid.uuid4()),
         "title": "社区图书角运营经验",
         "url": "https://example.com/community-library",
         "note": "社区图书角运营 5 年经验：定期轮换、主题月、亲子共读。",
         "mood": "启发", "created_at": _day_offset_ms(4, hour=14), "pushed_at": _day_offset_ms(4, hour=14), "pushed_by": 0},
        {"project_id": p3, "inspiration_uuid": str(uuid.uuid4()),
         "title": "法律咨询登记表模板",
         "url": "https://example.com/legal-form-template",
         "note": "标准化登记表模板，含隐私保护条款。",
         "mood": "实用", "created_at": _day_offset_ms(5, hour=11), "pushed_at": _day_offset_ms(5, hour=11), "pushed_by": 0},
        {"project_id": p1, "inspiration_uuid": str(uuid.uuid4()),
         "title": "老人院活动方案灵感",
         "url": "https://example.com/elderly-activities",
         "note": "10 个老人院活动方案：手工、书法、合唱、回忆录、节日主题。",
         "mood": "灵感", "created_at": _day_offset_ms(6, hour=13), "pushed_at": _day_offset_ms(6, hour=13), "pushed_by": 0},
        {"project_id": p2, "inspiration_uuid": str(uuid.uuid4()),
         "title": "图书分类标签设计灵感",
         "url": "https://example.com/book-tag-design",
         "note": "图书分类标签设计灵感：颜色编码 + 图标 + 年龄段标识。",
         "mood": "创意", "created_at": _day_offset_ms(8, hour=10), "pushed_at": _day_offset_ms(8, hour=10), "pushed_by": 0},
        {"project_id": p1, "inspiration_uuid": str(uuid.uuid4()),
         "title": "志愿者招募文案灵感",
         "url": "https://example.com/volunteer-recruit-copy",
         "note": "招募文案灵感：用故事代替口号，让招募启事有温度。",
         "mood": "温暖", "created_at": _day_offset_ms(12, hour=11), "pushed_at": _day_offset_ms(12, hour=11), "pushed_by": 0},
        {"project_id": p2, "inspiration_uuid": str(uuid.uuid4()),
         "title": "募捐启事文案参考",
         "url": "https://example.com/donation-appeal-copy",
         "note": "募捐启事文案参考：明确用途、透明账目、定期反馈。",
         "mood": "实用", "created_at": _day_offset_ms(12, hour=16), "pushed_at": _day_offset_ms(12, hour=16), "pushed_by": 0},
    ]


def _build_suggestions(project_ids: list[int]) -> list[dict]:
    """8 个 AgentSuggestion（4 种 type × 2 种 status）。

    Phase 6.1 叙事扩展：修复时间矛盾 + daily_summary body 与新任务对齐。
    分布：
        stale_warning:  P1 pending (D5), P2 shown (D1)
        overdue_task:   P1 pending, P2 acted
        handoff_gap:    P3 shown (D19), P3 dismissed (D20)（confirmed_at=D20 前后）
        daily_summary:  P2 shown, P3 acted
    """
    p1, p2, p3 = project_ids
    return [
        # stale_warning（Phase 6.1 修复：D0→D5，title 停滞 0 天→活跃度提醒）
        # 原 bug：D0 创建 + updated_at=D0 + title "停滞 0 天" 语义矛盾
        {"project_id": p1, "type": "stale_warning",
         "title": "项目活跃度提醒",
         "body": "项目「周末探访老人院」近 3 天活动减少，建议保持探访节奏，避免志愿者流失。",
         "severity": "warning", "status": "pending",
         "created_at": _day_offset(5, hour=18), "acted_at": None},
        {"project_id": p2, "type": "stale_warning",
         "title": "项目活跃度提醒",
         "body": "项目「山区图书角募捐」今日有活动更新，请保持跟进。",
         "severity": "warning", "status": "shown",
         "created_at": _day_offset(1, hour=18), "acted_at": None},
        # overdue_task
        {"project_id": p1, "type": "overdue_task",
         "title": "2 个任务超期",
         "body": "项目「周末探访老人院」有 2 个任务创建超 3 天未完成：周六下午探访执行（17 天）、老人兴趣档案建立（4 天）。",
         "severity": "warning", "status": "pending",
         "created_at": _day_offset(0, hour=19), "acted_at": None},
        {"project_id": p2, "type": "overdue_task",
         "title": "3 个任务超期",
         "body": "项目「山区图书角募捐」有 3 个任务创建超 3 天未完成：联系出版社捐赠（20 天）、图书分类标签制作（9 天）、物流配送安排（7 天）。",
         "severity": "critical", "status": "acted",
         "created_at": _day_offset(2, hour=10), "acted_at": _day_offset(1, hour=9)},
        # handoff_gap（Phase 6.1 修复：D10→D19，与 confirmed_at=D20 对齐）
        # 原 bug：D10 触发 gap 但 confirmed_at=D20，gap 在确认前 10 天就触发不合逻辑
        # 修复后：D19 触发（confirmed_at=D20 前一天），D20 复检（确认后 dismissed）
        {"project_id": p3, "type": "handoff_gap",
         "title": "交接缺口检测",
         "body": "项目「社区无偿法律援助」已完成但未确认交接，建议生成交接摘要供后续参考。",
         "severity": "info", "status": "shown",
         "created_at": _day_offset(19, hour=11), "acted_at": None},
        {"project_id": p3, "type": "handoff_gap",
         "title": "交接缺口复检",
         "body": "项目「社区无偿法律援助」已完成，交接摘要已确认，无需进一步操作。",
         "severity": "info", "status": "dismissed",
         "created_at": _day_offset(20, hour=11), "acted_at": None},
        # daily_summary（Phase 6.1 修复：body 与新任务时间对齐）
        # P3 daily_summary D9：原 body "项目启动任务完成" 与新 task D12 项目启动不对应
        # 修复后：D9 时 P3 已完成首场咨询(D10)→归档(D8)，body 改为"咨询案例归档完成"
        {"project_id": p2, "type": "daily_summary",
         "title": "今日活动日报",
         "body": "项目「山区图书角募捐」今日活动：1 个任务更新（下批图书清单更新），进展正常。",
         "severity": "info", "status": "shown",
         "created_at": _day_offset(8, hour=18), "acted_at": None},
        {"project_id": p3, "type": "daily_summary",
         "title": "今日活动日报",
         "body": "项目「社区无偿法律援助」今日活动：咨询案例归档完成（8 个案例卷宗已整理），进展顺利。",
         "severity": "info", "status": "acted",
         "created_at": _day_offset(9, hour=18), "acted_at": _day_offset(9, hour=19)},
    ]


def _build_sync_items() -> list[dict]:
    """8 个 SyncItem（5 focus snapshot + 3 muse inspiration）。

    用于 sync_by_app + sync_by_item_type + sync_active_users 指标。
    owner_id=0（dev 模式兼容，与现有 _seed_demo_data 一致）。
    """
    items = []
    # 5 个 Focus snapshot
    for i in range(5):
        items.append({
            "owner_id": 0,
            "app": "focus",
            "item_type": "snapshot",
            "client_uuid": str(uuid.uuid4()),
            "data": json.dumps({"title": f"Focus 快照 #{i+1}", "tab_count": 3 + i}),
            "client_created_at": _day_offset_ms(i, hour=10),
            "client_updated_at": _day_offset_ms(i, hour=10),
        })
    # 3 个 Muse inspiration
    for i in range(3):
        items.append({
            "owner_id": 0,
            "app": "muse",
            "item_type": "inspiration",
            "client_uuid": str(uuid.uuid4()),
            "data": json.dumps({"title": f"Muse 灵感 #{i+1}", "mood": "温暖"}),
            "client_created_at": _day_offset_ms(i, hour=11),
            "client_updated_at": _day_offset_ms(i, hour=11),
        })
    return items


# ============================================================
# 播种 / 重置
# ============================================================

def reset_demo_data(db: Session) -> dict:
    """清空所有演示数据（保留 VannevarMeta 表结构，但清空标记）。

    按依赖顺序删除（先删子表，再删父表）。
    """
    # Phase 4.1 表（依赖 projects，先删）
    db.query(ProjectContextInspiration).delete()
    db.query(ProjectContextSnapshot).delete()
    # 阶段2.2 表
    db.query(AgentSuggestion).delete()
    # 阶段1.5 表
    db.query(SyncItem).delete()
    # 阶段1 表
    db.query(Message).delete()
    db.query(Handoff).delete()
    db.query(Task).delete()
    # 阶段2.3 表
    db.query(ProjectShare).delete()
    db.query(TeamMembership).delete()
    db.query(OrgMembership).delete()
    db.query(Team).delete()
    db.query(OrgInvitation).delete()
    db.query(Organization).delete()
    # 阶段1 父表
    db.query(Project).delete()
    db.query(User).delete()
    # 重置 seeded 标记
    db.query(VannevarMeta).filter(VannevarMeta.key == "seeded").delete()
    db.query(VannevarMeta).filter(VannevarMeta.key == DEMO_META_KEY).delete()
    db.commit()
    return {"reset": True}


def seed_demo_data(db: Session, force: bool = False) -> dict:
    """播种演示数据。

    幂等性：通过 VannevarMeta[demo_seeded] 标记保证只播种一次。
        force=True 时强制重新播种（先 reset 再 seed）。

    R1b P2-3 修复：force=False 时绝不自动清空已有数据。
        若 demo_seeded=false 但 Project 表已有数据（可能是用户手动建的真实数据），
        返回 {"needs_reset": True, "reason": str} 让调用方显式决定是否清空，
        避免误删用户数据。调用方应先 POST /demo/reset 再 POST /demo/load。

    Returns:
        {"seeded": True, "counts": {...}}    — 新播种
        {"skipped": True, "reason": str}      — 已播种跳过
        {"needs_reset": True, "reason": str}  — 已有非 demo 数据，需显式 reset
    """
    # 幂等检查
    if not force:
        seeded = db.query(VannevarMeta).filter_by(key=DEMO_META_KEY).first()
        if seeded is not None and seeded.value == "true":
            return {"skipped": True, "reason": f"demo data already seeded (meta[{DEMO_META_KEY}]=true)"}

    # force=True 时显式清空（用户主动要求重新播种）
    if force:
        reset_demo_data(db)
    elif db.query(Project).count() > 0:
        # R1b P2-3 修复：force=False 但已有数据，不自动清空，要求显式 reset
        return {
            "needs_reset": True,
            "reason": (
                "database already contains project data (demo_seeded not set). "
                "POST /demo/reset first to clear all data, then POST /demo/load to seed."
            ),
        }

    counts = {}

    # 1. 项目
    projects_data = _build_projects()
    projects = [Project(**p) for p in projects_data]
    db.add_all(projects)
    db.flush()  # 获取 id
    project_ids = [p.id for p in projects]
    counts["projects"] = len(projects)

    # 2. 任务
    tasks_data = _build_tasks(project_ids)
    tasks = [Task(**t) for t in tasks_data]
    db.add_all(tasks)
    db.flush()
    # 建立 (project_id, title) → task_id 映射，供留言关联
    task_id_map = {(t.project_id, t.title): t.id for t in tasks}
    counts["tasks"] = len(tasks)

    # 3. 留言
    messages_data = _build_messages(project_ids, task_id_map)
    messages = [Message(**m) for m in messages_data]
    db.add_all(messages)
    counts["messages"] = len(messages)

    # 4. 交接摘要
    handoffs_data = _build_handoffs(project_ids)
    handoffs = [Handoff(**h) for h in handoffs_data]
    db.add_all(handoffs)
    counts["handoffs"] = len(handoffs)

    # 5. Focus 快照（Phase 4.1）
    snapshots_data = _build_snapshots(project_ids)
    snapshots = [ProjectContextSnapshot(**s) for s in snapshots_data]
    db.add_all(snapshots)
    counts["snapshots"] = len(snapshots)

    # 6. Muse 灵感（Phase 4.1）
    inspirations_data = _build_inspirations(project_ids)
    inspirations = [ProjectContextInspiration(**i) for i in inspirations_data]
    db.add_all(inspirations)
    counts["inspirations"] = len(inspirations)

    # 7. Agent 建议
    suggestions_data = _build_suggestions(project_ids)
    suggestions = [AgentSuggestion(**s) for s in suggestions_data]
    db.add_all(suggestions)
    counts["suggestions"] = len(suggestions)

    # 8. SyncItem（跨设备同步表）
    sync_items_data = _build_sync_items()
    sync_items = [SyncItem(**s) for s in sync_items_data]
    db.add_all(sync_items)
    counts["sync_items"] = len(sync_items)

    # 标记已播种
    db.add(VannevarMeta(key=DEMO_META_KEY, value="true"))
    # 同时标记 seeded（避免 init_db 的 _seed_demo_data 重复播种）
    db.add(VannevarMeta(key="seeded", value="true"))

    db.commit()
    return {"seeded": True, "counts": counts}


# ============================================================
# CLI 入口
# ============================================================

def main() -> int:
    parser = argparse.ArgumentParser(description="Phase 5 演示数据集生成脚本")
    parser.add_argument("--reset", action="store_true",
                        help="清空所有数据后重新播种（等同 --force）")
    parser.add_argument("--force", action="store_true",
                        help="强制重新播种（等同 --reset，即使已标记 demo_seeded）")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    db = SessionLocal()
    try:
        if args.reset or args.force:
            print("[1/2] 清空现有数据...")
            reset_result = reset_demo_data(db)
            print(f"  reset: {reset_result}")

        print("[2/2] 播种演示数据...")
        result = seed_demo_data(db, force=args.reset or args.force)
        print(f"  result: {result}")
        return 0 if ("seeded" in result or "skipped" in result) else 1
    except Exception as e:
        logger.exception("播种失败")
        print(f"ERROR: {e}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
