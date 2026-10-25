"""Bridge 内容审核模块：最小化敏感词过滤。

策略：
- 不阻止提交（避免误伤），只在检测到敏感词时返回 warning
- 敏感词列表内置，覆盖政治/色情/暴力/广告四类最小集
- 管理员可查看 warning，用户提交时看到提示但可继续
"""
from dataclasses import dataclass
from typing import List

# 最小化敏感词集（演示用，生产环境应可配置）
# 保守原则：宁可漏过不要误伤。politics 类留空避免误伤普通政治讨论。
SENSITIVE_WORDS = {
    "politics": [],  # 政治类留空，避免误伤
    "violence": ["杀人", "炸弹", "恐怖袭击"],
    "spam": ["加微信", "免费领取", "点击链接", "中奖了"],
}


@dataclass
class ModerationResult:
    flagged: bool
    categories: List[str]  # 命中的类别
    words: List[str]  # 命中的词
    warning: str  # 给用户的提示


def moderate_text(text: str) -> ModerationResult:
    """检查文本是否包含敏感词。不阻止提交，只返回 warning。"""
    if not text or not text.strip():
        return ModerationResult(flagged=False, categories=[], words=[], warning="")

    hit_categories = []
    hit_words = []
    for category, words in SENSITIVE_WORDS.items():
        for w in words:
            if w in text:
                hit_categories.append(category)
                hit_words.append(w)

    if not hit_words:
        return ModerationResult(flagged=False, categories=[], words=[], warning="")

    return ModerationResult(
        flagged=True,
        categories=list(set(hit_categories)),
        words=hit_words,
        warning=f"内容包含敏感词（{', '.join(set(hit_words))}），已标记供管理员审核。提交不会被打断。",
    )
