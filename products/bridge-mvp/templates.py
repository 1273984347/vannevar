"""Bridge 交接模板系统：让 Bridge 从公益工具升级为通用交接工具。

支持四种交接模板：
- volunteer    公益志愿者交接
- resignation  离职交接
- outsourcing  外包交付交接
- opensource   开源维护者更替
"""
from dataclasses import dataclass
from typing import List


@dataclass
class HandoffTemplate:
    id: str
    name: str
    description: str
    # 模板特有的字段标签（用于 UI 提示）
    fields: List[str]  # 如 ["项目背景", "待办事项", "注意事项"]
    # 摘要占位符（用户点"自动生成"时，规则引擎按此结构填充）
    summary_sections: List[str]  # 如 ["未完成任务", "近 7 天留言", "进度概览"]
    icon: str  # SVG path data (24x24 viewBox)


TEMPLATES = {
    "volunteer": HandoffTemplate(
        id="volunteer",
        name="公益志愿者交接",
        description="志愿者轮替时的项目交接，重点是人带人",
        fields=["项目背景", "待办事项", "联系人", "注意事项"],
        summary_sections=["未完成任务", "近 7 天留言", "进度概览"],
        icon='<polyline points="17 1 21 5 17 9"/><path d="M3 11V9a4 4 0 0 1 4-4h14"/><polyline points="7 23 3 19 7 15"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>',
    ),
    "resignation": HandoffTemplate(
        id="resignation",
        name="离职交接",
        description="员工离职时的工作移交，重点是文档和权限",
        fields=["职责范围", "进行中项目", "文档清单", "权限移交", "联系人"],
        summary_sections=["进行中任务", "近 7 天留言", "文档清单提示"],
        icon='<path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><rect x="8" y="2" width="8" height="4" rx="1"/>',
    ),
    "outsourcing": HandoffTemplate(
        id="outsourcing",
        name="外包交付交接",
        description="外包项目交付时的上下文移交，重点是验收标准",
        fields=["交付物清单", "验收标准", "技术文档", "遗留问题", "付款节点"],
        summary_sections=["待验收任务", "近 7 天留言", "交付物概览"],
        icon='<path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"/>',
    ),
    "opensource": HandoffTemplate(
        id="opensource",
        name="开源维护者更替",
        description="开源项目维护者断档时的接力，重点是社区和路线图",
        fields=["项目状态", "社区成员", "版本路线图", "已知问题", "权限移交"],
        summary_sections=["未完成任务", "近 7 天留言", "版本状态"],
        icon='<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/>',
    ),
}

DEFAULT_TEMPLATE_ID = "volunteer"

# 允许的模板 ID 集合，供 schema 校验复用
ALLOWED_TEMPLATE_IDS = set(TEMPLATES.keys())


def get_template(template_id: str) -> HandoffTemplate:
    return TEMPLATES.get(template_id, TEMPLATES[DEFAULT_TEMPLATE_ID])


def get_all_templates() -> List[HandoffTemplate]:
    return list(TEMPLATES.values())
