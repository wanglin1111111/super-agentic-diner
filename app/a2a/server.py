# -*- coding: utf-8 -*-
"""
A2A Protocol 适配器 —— 实现 Google Agent-to-Agent Protocol

提供三个核心端点：
  1. Agent Card（/.well-known/agent.json）— Agent 自描述，供其他 Agent 发现
  2. Task CRUD（/api/a2a/tasks/{task_id}）— 创建和查询异步任务
  3. Message（/api/a2a/tasks/{task_id}/messages）— 向任务发送消息

任务执行逻辑复用 app/skill/service.py 中的函数。
"""
from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.skill.service import (
    order_coffee_via_skill,
    get_menu_via_skill,
    check_points_via_skill,
    query_order_via_skill,
)

logger = logging.getLogger("a2a.protocol")

# ============================================================
# A2A 数据结构（Dataclass）
# ============================================================


class TaskStatus(str, Enum):
    """A2A 任务状态机：submitted -> working -> completed / failed"""
    SUBMITTED = "submitted"
    WORKING = "working"
    COMPLETED = "completed"
    FAILED = "failed"


class MessageRole(str, Enum):
    """A2A 消息角色"""
    USER = "user"
    AGENT = "agent"


@dataclass
class Part:
    """消息内容片段（文本或文件引用）"""
    text: Optional[str] = None
    file_url: Optional[str] = None
    metadata: dict = field(default_factory=dict)


@dataclass
class Artifact:
    """任务产出物"""
    name: str
    parts: list[Part] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class Message:
    """A2A 消息：Agent 间交互的基本单位"""
    role: str
    parts: list[Part] = field(default_factory=list)
    message_id: str = ""
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.message_id:
            self.message_id = f"msg-{uuid.uuid4().hex[:12]}"
        if not self.timestamp:
            self.timestamp = time.time()

    def to_dict(self) -> dict:
        return {
            "messageId": self.message_id,
            "role": self.role,
            "parts": [
                {"text": p.text, "fileUrl": p.file_url, "metadata": p.metadata}
                for p in self.parts
            ],
            "timestamp": self.timestamp,
        }


@dataclass
class Task:
    """A2A 异步任务"""
    task_id: str = ""
    status: str = TaskStatus.SUBMITTED
    user_id: Optional[int] = None
    messages: list[Message] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        if not self.task_id:
            self.task_id = f"task-{uuid.uuid4().hex[:16]}"
        if not self.created_at:
            self.created_at = time.time()
        if not self.updated_at:
            self.updated_at = self.created_at

    def to_dict(self) -> dict:
        return {
            "taskId": self.task_id,
            "status": self.status,
            "userId": self.user_id,
            "messages": [m.to_dict() for m in self.messages],
            "artifacts": [
                {
                    "name": a.name,
                    "parts": [
                        {"text": p.text, "fileUrl": p.file_url}
                        for p in a.parts
                    ],
                    "metadata": a.metadata,
                }
                for a in self.artifacts
            ],
            "metadata": self.metadata,
            "createdAt": self.created_at,
            "updatedAt": self.updated_at,
        }


@dataclass
class AgentSkill:
    """Agent Card 中声明的 Skill 端点"""
    id: str
    name: str
    description: str
    endpoint: str
    tags: list[str] = field(default_factory=list)


@dataclass
class AgentCapabilities:
    """Agent 能力声明"""
    streaming: bool = False
    push_notifications: bool = False


@dataclass
class AgentCard:
    """A2A Agent Card —— Agent 自描述，供其他 Agent 发现"""
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    capabilities: AgentCapabilities = field(default_factory=AgentCapabilities)
    skills: list[AgentSkill] = field(default_factory=list)
    default_input_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    default_output_modes: list[str] = field(default_factory=lambda: ["text/plain"])
    provider: dict = field(default_factory=lambda: {"organization": "A2A Super Diner Team"})

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "url": self.url,
            "version": self.version,
            "capabilities": {
                "streaming": self.capabilities.streaming,
                "pushNotifications": self.capabilities.push_notifications,
            },
            "skills": [
                {
                    "id": s.id,
                    "name": s.name,
                    "description": s.description,
                    "endpoint": s.endpoint,
                    "tags": s.tags,
                }
                for s in self.skills
            ],
            "defaultInputModes": self.default_input_modes,
            "defaultOutputModes": self.default_output_modes,
            "provider": self.provider,
        }


# ============================================================
# 内存任务存储
# ============================================================

_tasks_store: dict[str, Task] = {}


def _get_task(task_id: str) -> Task:
    """获取任务，不存在则 404"""
    if task_id not in _tasks_store:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    return _tasks_store[task_id]


# ============================================================
# Pydantic 请求/响应模型
# ============================================================


class A2AMessageRequest(BaseModel):
    """发送消息请求体"""
    role: str = "user"
    text: str = ""
    metadata: dict = {}


class A2ATaskCreateRequest(BaseModel):
    """创建任务请求体"""
    user_id: int
    message: str = ""
    skill: Optional[str] = None  # 指定调用的 skill id，不指定则走 chat 模式
    params: dict = {}


# ============================================================
# 路由定义
# ============================================================

router = APIRouter(prefix="/api/a2a", tags=["A2A Protocol"])


# ------ Agent Card（必须在根路径，遵循 A2A 规范） ------

# Agent Card 不使用 prefix，单独创建
agent_card_router = APIRouter(tags=["A2A Protocol"])


@agent_card_router.get(
    "/.well-known/agent.json",
    summary="A2A Agent Card",
    description="返回 Agent 自描述信息（名称、描述、能力、Skill 端点等），供其他 Agent 发现和交互",
)
async def get_agent_card(request_url: str = ""):
    """Agent Card 端点：遵循 A2A Protocol 的 agent discovery 机制。

    其他 Agent 访问 /.well-known/agent.json 即可获取本 Agent 的元数据，
    包括能力声明、可用 Skill 接口等信息。
    """
    card = AgentCard(
        name="A2A Super Diner",
        description="超级点单系统 Agent，支持咖啡点单、游戏可视化、积分管理",
        url=request_url or "http://localhost:8000",
        capabilities=AgentCapabilities(
            streaming=False,
            push_notifications=False,
        ),
        skills=[
            AgentSkill(
                id="order_coffee",
                name="咖啡点单",
                description="通过 Agent 下单咖啡（消耗积分，自动确认，触发游戏动画）",
                endpoint="/api/v1/skill/order",
                tags=["order", "coffee", "points"],
            ),
            AgentSkill(
                id="get_menu",
                name="获取菜单",
                description="查询完整的咖啡菜单（名称、价格、标签、描述）",
                endpoint="/api/v1/skill/menu",
                tags=["menu", "coffee", "query"],
            ),
            AgentSkill(
                id="check_points",
                name="查询积分",
                description="查询用户积分余额和账户信息",
                endpoint="/api/v1/skill/points/{user_id}",
                tags=["points", "query", "balance"],
            ),
            AgentSkill(
                id="query_order",
                name="查询订单",
                description="根据订单 ID 查询订单状态",
                endpoint="/api/v1/skill/order/{order_id}",
                tags=["order", "query", "status"],
            ),
            AgentSkill(
                id="place_order_task",
                name="异步点单任务",
                description="通过 A2A Task 协议异步执行点单（创建任务 -> 发送消息 -> 获取结果）",
                endpoint="/api/a2a/tasks",
                tags=["a2a", "task", "async", "order"],
            ),
        ],
    )
    return card.to_dict()


# ------ Task CRUD ------

@router.post(
    "/tasks",
    summary="创建 A2A 任务",
    description="创建一个新的 A2A 异步任务，可直接传入消息并指定要调用的 Skill",
    status_code=201,
)
def create_task(
    req: A2ATaskCreateRequest,
    db: Session = Depends(get_db),
):
    """创建 A2A 任务。

    流程：
      1. 创建 Task（状态: submitted）
      2. 如果携带 message，将其作为首条消息加入
      3. 如果指定了 skill，直接调用对应的 skill/service 函数
      4. 返回 Task 完整状态
    """
    task = Task(
        user_id=req.user_id,
        metadata={"skill": req.skill or "", "params": req.params},
    )

    # 添加首条消息（如果有）
    if req.message:
        task.messages.append(Message(
            role="user",
            parts=[Part(text=req.message)],
        ))

    _tasks_store[task.task_id] = task

    # 如果指定了 skill 或有消息，尝试执行
    if req.message:
        task.status = TaskStatus.WORKING
        task.updated_at = time.time()
        result = _execute_task_message(task, db)
        # result 是处理后的结果描述
        task.messages.append(Message(
            role="agent",
            parts=[Part(text=result)],
        ))
        # 如果执行成功且有产出，添加 artifact
        if task.status == TaskStatus.COMPLETED:
            task.artifacts.append(Artifact(
                name="order_result",
                parts=[Part(text=result)],
                metadata={"source": "a2a_task"},
            ))

    return task.to_dict()


@router.get(
    "/tasks/{task_id}",
    summary="获取 A2A 任务状态",
    description="查询指定任务的状态、消息历史和产出物",
)
def get_task(task_id: str):
    """获取 A2A 任务详情。"""
    task = _get_task(task_id)
    return task.to_dict()


@router.delete(
    "/tasks/{task_id}",
    summary="取消 A2A 任务",
    description="取消/删除一个 A2A 任务",
)
def cancel_task(task_id: str):
    """取消 A2A 任务（标记为 failed 并从存储中移除）。"""
    task = _get_task(task_id)
    task.status = TaskStatus.FAILED
    task.updated_at = time.time()
    task.messages.append(Message(
        role="agent",
        parts=[Part(text="任务已被取消")],
    ))
    return {"ok": True, "taskId": task_id, "status": "cancelled"}


# ------ Message ------

@router.post(
    "/tasks/{task_id}/messages",
    summary="向 A2A 任务发送消息",
    description="向指定任务追加消息并触发执行",
    status_code=201,
)
def send_message(
    task_id: str,
    req: A2AMessageRequest,
    db: Session = Depends(get_db),
    user_id: Optional[int] = Query(None, description="用户 ID（可选，覆盖创建时的值）"),
):
    """向已有 A2A 任务发送新消息并执行。

    流程：
      1. 追加用户消息到 task.messages
      2. 将任务状态设为 working
      3. 解析消息内容，调用对应 skill/service 函数
      4. 将 Agent 回复追加到 task.messages
      5. 更新任务状态为 completed/failed
    """
    task = _get_task(task_id)

    # 如果传了 user_id 参数，更新任务关联
    if user_id is not None:
        task.user_id = user_id

    # 追加用户消息
    user_msg = Message(
        role=req.role or "user",
        parts=[Part(text=req.text)],
    )
    task.messages.append(user_msg)

    # 执行任务
    task.status = TaskStatus.WORKING
    task.updated_at = time.time()
    result = _execute_task_message(task, db)

    # 追加 Agent 回复
    task.messages.append(Message(
        role="agent",
        parts=[Part(text=result)],
    ))

    if task.status == TaskStatus.COMPLETED:
        task.artifacts.append(Artifact(
            name="response",
            parts=[Part(text=result)],
            metadata={"round": len(task.messages) // 2},
        ))

    return task.to_dict()


@router.get(
    "/tasks/{task_id}/messages",
    summary="获取 A2A 任务的消息列表",
    description="获取指定任务的所有消息记录",
)
def list_messages(task_id: str):
    """获取任务的消息历史。"""
    task = _get_task(task_id)
    return {
        "taskId": task.task_id,
        "messages": [m.to_dict() for m in task.messages],
    }


# ============================================================
# 任务执行引擎（复用 skill/service.py）
# ============================================================

def _execute_task_message(task: Task, db: Session) -> str:
    """解析最新的用户消息，调用对应的 skill/service 函数，返回 Agent 回复文本。

    支持的消息格式：
      - "点一杯 拿铁" / "order 拿铁" → 调用 order_coffee_via_skill
      - "菜单" / "menu" → 调用 get_menu_via_skill
      - "积分" / "points" → 调用 check_points_via_skill
      - "查订单 123" / "order 123" → 调用 query_order_via_skill
      - 其他 → 走通用 chat 处理
    """
    if not task.messages:
        task.status = TaskStatus.FAILED
        task.updated_at = time.time()
        return "错误：没有消息可处理"

    last_msg = task.messages[-1]
    text = ""
    for p in last_msg.parts:
        if p.text:
            text = p.text
            break

    if not text.strip():
        task.status = TaskStatus.FAILED
        task.updated_at = time.time()
        return "错误：消息内容为空"

    text_lower = text.strip().lower()
    user_id = task.user_id

    try:
        # ---- 路由到对应 skill 函数 ----

        # 1. 菜单查询
        if any(kw in text_lower for kw in ("菜单", "menu", "有什么咖啡")):
            result = get_menu_via_skill(db)
            task.status = TaskStatus.COMPLETED
            task.updated_at = time.time()
            lines = []
            for item in result.get("menu", []):
                lines.append(f"  {item['name']}  ¥{item['price']:.2f}  {item.get('tags', '')}")
            return f"菜单如下（每单消耗 {result.get('points_per_order', 10)} 积分）：\n" + "\n".join(lines)

        # 2. 积分查询
        if any(kw in text_lower for kw in ("积分", "points", "余额")):
            if not user_id:
                task.status = TaskStatus.FAILED
                task.updated_at = time.time()
                return "错误：查询积分需要提供 user_id"
            result = check_points_via_skill(db, user_id)
            task.status = TaskStatus.COMPLETED
            task.updated_at = time.time()
            return (
                f"用户 {result['nickname']} 的积分信息：\n"
                f"  积分: {result['points']}\n"
                f"  余额: ¥{result['balance']:.2f}"
            )

        # 3. 订单查询
        if any(kw in text_lower for kw in ("查订单", "order status", "订单状态")):
            # 尝试从消息中提取 order_id
            import re
            nums = re.findall(r'\d+', text)
            if nums:
                result = query_order_via_skill(db, int(nums[-1]))
                task.status = TaskStatus.COMPLETED
                task.updated_at = time.time()
                if "error" in result:
                    return f"查询失败：{result['error']}"
                return (
                    f"订单 {result['order_id']}：\n"
                    f"  咖啡: {result['coffee_name']}\n"
                    f"  金额: ¥{result['amount']:.2f}\n"
                    f"  状态: {result['status']}\n"
                    f"  时间: {result['created_at']}"
                )
            else:
                return "请提供订单号，例如：查订单 123"

        # 4. 点单（默认意图）
        if any(kw in text_lower for kw in ("点", "来杯", "来个", "买", "下单", "order", "要一杯")):
            if not user_id:
                task.status = TaskStatus.FAILED
                task.updated_at = time.time()
                return "错误：点单需要提供 user_id"

            # 提取咖啡名（去掉命令前缀词）
            coffee_name = _extract_coffee_name(text)
            if not coffee_name:
                task.status = TaskStatus.FAILED
                task.updated_at = time.time()
                return "无法识别咖啡名，请重新描述您想点的咖啡"

            result = order_coffee_via_skill(
                db,
                user_id=user_id,
                coffee_name=coffee_name,
                agent_type="a2a",
            )
            task.updated_at = time.time()

            if result.get("error"):
                task.status = TaskStatus.FAILED
                if "available" in result:
                    avail = result["available"]
                    return f"点单失败：{result['error']}。可用咖啡：{', '.join(avail)}"
                return f"点单失败：{result['error']}"

            task.status = TaskStatus.COMPLETED
            task.metadata["last_order_id"] = result.get("order_id")
            return (
                f"{result.get('message', '点单成功！')}\n"
                f"  订单号: {result.get('order_id')}\n"
                f"  咖啡: {result.get('coffee_name')}\n"
                f"  消耗积分: {result.get('points_cost')}\n"
                f"  剩余积分: {result.get('balance_after')}"
            )

        # 5. 通用回复
        task.status = TaskStatus.COMPLETED
        task.updated_at = time.time()
        return (
            f"收到消息：{text}\n\n"
            "我是 A2A Super Diner，支持以下指令：\n"
            "  - 菜单 / menu — 查看咖啡菜单\n"
            "  - 积分 / points — 查询积分余额\n"
            "  - 点一杯拿铁 — 点单\n"
            "  - 查订单 123 — 查询订单状态"
        )

    except Exception as e:
        logger.error(f"[A2A] 任务执行失败: {e}", exc_info=True)
        task.status = TaskStatus.FAILED
        task.updated_at = time.time()
        return f"执行失败：{e}"


def _extract_coffee_name(text: str) -> str:
    """从消息中提取咖啡名，去掉常见的命令前缀"""
    prefixes = [
        "点一杯", "点一", "来一杯", "来个", "来杯",
        "我要", "帮我点", "买一杯", "下单", "我要一杯",
        "order", "点", "要", "来", "买",
    ]
    name = text.strip()
    for prefix in prefixes:
        if name.lower().startswith(prefix):
            name = name[len(prefix):].strip()
            break
    # 去掉多余的量词
    for suffix in ["吧", "啊", "。", "，", "！", "？", "."]:
        if name.endswith(suffix):
            name = name[:-1].strip()
    return name
