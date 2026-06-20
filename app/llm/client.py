"""LLM 客户端：OpenAI 兼容协议，用 httpx 直连

原则：LLM 只负责「理解」和「说话」，绝不直接操作数据库。
所有写库动作（扣款、下单）都由 services 层在事务里完成。

采用 httpx 直连而非 openai SDK，避免 SDK 与 httpx 版本冲突。
"""
from __future__ import annotations

import json
import time

import httpx

from app.config import settings

_client: httpx.Client | None = None


def get_client() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(timeout=60.0)
    return _client


def has_real_key() -> bool:
    """是否配置了真实 LLM key（非占位符）"""
    k = settings.llm_api_key.strip()
    return bool(k) and k != "sk-your-key-here" and not k.startswith("sk-your")


def _call_llm(messages):
    """统一调用入口：POST {base_url}/chat/completions
    遇到 429 限流时自动重试 1 次（等 1.5 秒）。
    """
    url = settings.llm_base_url.rstrip("/") + "/chat/completions"
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }
    payload = {"model": settings.llm_model, "messages": messages, "temperature": 0.7}

    for attempt in range(2):  # 最多 2 次（首次 + 1 次重试）
        try:
            resp = get_client().post(url, headers=headers, json=payload)
        except httpx.HTTPError:
            if attempt == 0:
                time.sleep(1.5)
                continue
            raise
        # 429 限流：等一下重试一次
        if resp.status_code == 429 and attempt == 0:
            time.sleep(1.5)
            continue
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    # 两次都 429
    raise httpx.HTTPStatusError("rate limited (429)", request=resp.request, response=resp)


# ============================================================
# 【面试题·四个基础组件之一：LLM 大语言模型】
#
# LLM 的两个核心职责：
#   1. 理解用户意图（parse_intent）：是下单？是求推荐？还是闲聊？
#   2. 生成对话回复（chat）：基于 RAG 检索到的资料，生成自然语言推荐
#
# 安全原则：LLM 只负责"理解"和"说话"，绝不直接操作数据库。
#           所有写库动作（扣款、下单）都由 order_service 在事务里完成。
# ============================================================

# 【任务二·LLM】系统提示词：定义 AI 店长的人设和行为规则
# 包含禁编造规则，防止 LLM 凭空创造不存在的咖啡/套餐/价格
SYSTEM_PROMPT = (
    "你是「智能咖啡馆 AI 店长」，友好、专业、简洁。"
    "你会收到用户的最新消息、最近几轮对话上下文，以及从《咖啡风味手册》检索到的咖啡知识段落。"
    "请基于检索到的资料进行推荐或回答。"
    "【重要规则】"
    "1. 只能推荐知识库中明确列出的咖啡，不得编造任何手册里没有的咖啡或饮品。"
    "2. 不得编造套餐、组合、搭配、轻食、赠品等知识库里没有的内容。"
    "3. 不得自行拼凑价格或组合优惠，所有价格必须与知识库一致。"
    "4. 如果用户问的内容（如套餐、外卖、会员卡）知识库里没有，请诚实告知没有，并引导到现有的咖啡选择。"
    "推荐时请给出咖啡名称、价格和简短风味说明。回复控制在150字以内。"
)


def chat(history, user_msg, context):
    """【任务二·LLM】基于 RAG 检索到的资料 + 对话历史，生成自然语言回复

    参数：
      history: Redis 读取的最近5轮对话（让 AI 有短期记忆）
      user_msg: 用户当前消息
      context: RAG 检索到的咖啡知识段落（来自 MySQL 的 CoffeeKB 表）

    流程：system提示 + RAG资料 + 对话历史 + 用户消息 → LLM 生成回复
    LLM 限流/失败时 → 优雅降级到 RAG 模板（用户无感）
    """
    if not has_real_key():
        return _mock_chat(context)
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if context:
        messages.append(
            {"role": "system", "content": f"《咖啡风味手册》相关段落：\n{context}"}
        )
    messages.extend(history)
    messages.append({"role": "user", "content": user_msg})
    try:
        return _call_llm(messages)
    except Exception:
        # LLM 限流/失败时，回退到用 RAG 检索结果直接回复（用户无感）
        return _mock_chat(context)


def _mock_chat(context: str) -> str:
    """无 LLM key 时的降级：直接用 RAG 检索结果拼推荐"""
    if not context:
        return "您好，我是咖啡馆 AI 店长！请问您想喝什么口味的咖啡？可以告诉我喜欢的风味或忌口哦~"
    return "根据您的喜好，为您推荐：\n" + context + "\n\n请问想点哪一杯呢？告诉我就可以为您下单啦~"


# 【任务三·LLM】意图分类提示词：让 LLM 把用户消息分为三种意图
# 这是任务三"系统执行步骤"的第一步——LLM 理解用户想干什么
# 三分类：order（下单）/ recommend（求推荐）/ chat（闲聊）
INTENT_PROMPT = """你是对话意图分类器。结合对话历史，判断用户最新消息的意图，输出三分类之一：

## 三种意图

**order** — 用户想购买/确认下单。典型特征：
- 直接说要买/下单/结账/扣钱：「就买这杯」「下单」「结账」
- 推荐后用简短肯定词回应：「可以」「行」「好的」「嗯」「要」「就这个」「就它了」「这两杯了」
- 判断关键：消息简短 + 肯定/确认语气 + 对话历史中刚推荐过咖啡

**recommend** — 用户想看推荐/描述口味偏好/问选择。典型特征：
- 描述口味：「我想喝果味的」「不要牛奶」「苦一点的」
- 要求推荐：「有什么推荐」「再来个」「换个口味」

**chat** — 其他闲聊/询问。典型特征：
- 问问题：「可以加冰吗」「几点关门」「多少钱」
- 肯定词 + 附加内容：「可以加点糖吗」（不是纯确认，有附加需求）
- 完全无关内容

## 关键判别规则
- 短肯定词（可以/行/好）+ 历史里有推荐 → order
- 短肯定词 + 历史里无推荐 → chat（应反问用户想买什么）
- 描述口味/问推荐 → recommend（即使用户说得很肯定）

## 输出格式（只输出 JSON，无其他文字）
{"intent":"order","reason":"简短理由","coffee_name":"咖啡名(来自上下文，可能为空)","quantity":1}
{"intent":"recommend","reason":"简短理由"}
{"intent":"chat","reason":"简短理由"}
"""

# LLM 不可用时的极简兜底词（只保留最硬的3个，覆盖基本场景）
_FALLBACK_ORDER_WORDS = ("买", "下单", "结账")


def parse_intent(history, user_msg):
    """【任务三·第2步】LLM 意图解析 —— 理解用户想干什么（不碰数据库）

    返回示例：
        {"intent":"order","coffee_name":"柑橘冷萃","quantity":1}   ← 用户想下单
        {"intent":"recommend"}                                      ← 用户想看推荐
        {"intent":"chat"}                                           ← 闲聊/询问

    这是纯 LLM 语义驱动（不再用硬编码触发词），能理解：
      "可以"（推荐后）→ order
      "可以加冰吗" → chat（有附加需求，不是纯确认）
      "这两杯了" → order
    """
    # LLM 可用：完全信任语义理解
    if has_real_key():
        messages = [{"role": "system", "content": INTENT_PROMPT}]
        messages.extend(history)
        messages.append({"role": "user", "content": user_msg})
        try:
            text = _call_llm(messages)
            text = _strip_code_fence(text)
            result = json.loads(text)
            # 规范化 intent 字段（兼容旧值 place_order）
            intent = result.get("intent", "")
            if intent in ("place_order", "order"):
                result["intent"] = "order"
            elif intent not in ("order", "recommend", "chat"):
                result["intent"] = "chat"
            return result
        except Exception:
            pass  # LLM 调用/解析失败，走兜底

    # 兜底：LLM 不可用时用关键词 + 咖啡名匹配判断意图
    # 1. 含下单硬词 → order
    if any(w in user_msg for w in _FALLBACK_ORDER_WORDS):
        return {"intent": "order"}
    # 2. 含"来杯/帮我/给我" + 咖啡相关词 → order（如"来杯美式"）
    _ORDER_PATTERNS = ("来杯", "来个", "帮我来", "给我来", "我要", "点一杯", "点一杯")
    _COFFEE_HINTS = ("咖啡", "拿铁", "美式", "冷萃", "玛奇朵", "摩卡", "卡布")
    if any(p in user_msg for p in _ORDER_PATTERNS):
        return {"intent": "order"}
    if any(h in user_msg for h in _COFFEE_HINTS) and len(user_msg) <= 10:
        return {"intent": "order"}
    return {"intent": "chat"}


def _strip_code_fence(text):
    """去掉 LLM 输出可能的 ```json 包裹"""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    return text.strip()
