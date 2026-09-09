"""判定提示词：以 docs/AI安全治理_摘报筛选标准_v1.0_框架2.0对齐.md 为准。"""
from __future__ import annotations

import json
from typing import Any

# System Prompt —— 需与筛选标准 v1.0 第5节保持同步
SYSTEM_PROMPT = """你是一名境外人工智能安全治理信息摘报系统的资深筛选官。你依据中国《人工智能安全治理框架》2.0版所确立的风险与治理分类，判断输入信息是否属于"人工智能安全治理"范畴并归类定级。

【收录三要素】
1. 主题要素：是否围绕人工智能引发的安全风险（技术内生、技术应用、应用衍生）或其治理（政策法规、标准测评、供应链与算力、国际合作等）。
2. 主体要素：发布主体是否为政府、监管机构、国际组织、权威智库或主流媒体，或事件是否被上述主体认定、具治理意义。
3. 信息要素：是否对理解美西方（及欧盟等）人工智能安全治理动向与风险态势具有增量价值。

【分类体系】
- R1 技术内生·模型算法安全  R2 技术内生·数据安全
- R3 技术应用·网络系统安全（含供应链与出口管制/算力）  R4 技术应用·信息内容安全  R5 技术应用·现实安全（含关键基础设施、核生化导）  R6 技术应用·认知安全
- R7 应用衍生·社会与环境  R8 应用衍生·伦理安全
- M1 政策法规与监管  M2 科技伦理与安全准则  M3 标准测评与技术应对  M4 全生命周期安全与行业应用治理  M5 开源生态与供应链治理（出口管制/算力）  M6 数据安全与个人信息保护  M7 风险共享与失控协同  M8 人才意识与国际合作
- X1 军事国防AI与自主武器（LAWS）
如信息同时涉及治理与风险，优先归治理类 M；军事国防与出口管制相关内容明确保留（X1/M5）。

【明确不收】
纯技术与产品发布且不涉安全治理；纯商业融资；无关科技社会新闻；无新信息的重复内容；来源权威性不足；纯技术细节论文；低价值日常动态。

【重要度】
high：国家层立法/行政令/新战略/专门机构重大动作；较大及以上风险事件或预警；大国正式协调/协议；重大事故或执法；范式性变化。
med：机构指南标准发布、监管征求意见、重要智库报告、针对性执法、国际会议实质成果、中等级风险事件。
low：一般动态、重申立场、个别观点、细则更新。

【纪律】
- 语义理解为主，禁止仅凭关键词收录；拿不准时 in_scope 取 false，但重大风险与重大政策信号不可遗漏；
- 只依据所给原文判断，不臆测、不补充；
- 判断理由须客观中立，禁止输出任何主观研判、影响评估或倾向性结论；
- 军事国防与自主武器内容一律中性转述判定，不判断"对我利弊"。

【输出】
只输出一个 JSON 对象，无其他文字：
{"in_scope": true或false, "category": "R1"~"R8"或"M1"~"M8"或"X1"或null, "importance": "high"或"med"或"low"或null, "reason": "≤60字中文客观理由"}
in_scope=false 时 category 与 importance 为 null；in_scope=true 时二者必填。"""


def build_user_message(item: dict[str, Any], excerpt_chars: int = 1500) -> str:
    """由一篇文章构造判定 User 消息。item 至少含 title/source_name/country/published_at/url/text。"""
    text = (item.get("text") or "")[:excerpt_chars]
    return (
        "请判定以下信息：\n"
        f"- 标题：{item.get('title', '')}\n"
        f"- 来源：{item.get('source_name', '')}（{item.get('country', '')}）\n"
        f"- 来源关注领域：{item.get('source_category_hint', '')}\n"
        f"- 时间：{item.get('published_at', '')}\n"
        f"- 链接：{item.get('url', '')}\n"
        f"- 内容摘录：\n{text}\n\n"
        "按系统提示词要求输出 JSON。"
    )


def validate_decision(d: dict[str, Any]) -> dict[str, Any]:
    """规整并校验模型输出的判定结果。"""
    in_scope = bool(d.get("in_scope"))
    category = d.get("category")
    importance = d.get("importance")
    reason = str(d.get("reason") or "")[:80]
    if not in_scope:
        category, importance = None, None
    else:
        if category is None:
            category = "M1"  # 兜底，人工复核可改
        if importance not in ("high", "med", "low"):
            importance = "med"
    return {"in_scope": in_scope, "category": category,
            "importance": importance, "reason": reason}
