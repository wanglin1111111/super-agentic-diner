# -*- coding: utf-8 -*-
"""
游戏状态机
管理点单过程中角色的动作序列（顾客走动 → 点单 → 等待 → 取餐）

核心设计：每次用户消息/Skill点单触发一个"导演脚本"，
按时间线推进角色动作，通过 WebSocket 广播给前端
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from app.game.actors import (
    Actor,
    CustomerState,
    StaffState,
    SCENE_POSITIONS,
    create_customer,
    create_waiter,
    create_barista,
    create_cashier,
)


# ============================================================
# 游戏事件（通过 WebSocket 广播给前端）
# ============================================================
@dataclass
class GameEvent:
    """一个游戏事件，驱动前端角色动画"""
    event_type: str           # ACTOR_SPAWN / ACTOR_MOVE / ACTOR_ANIMATE / ACTOR_SPEAK / SCENE_UPDATE
    actor_id: str             # 涉及的角色ID
    data: dict = field(default_factory=dict)  # 事件数据（位置、动画、气泡等）
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "type": self.event_type,
            "actor_id": self.actor_id,
            "data": self.data,
            "timestamp": self.timestamp,
        }


# ============================================================
# 点单导演脚本
# 当用户发送消息或 Skill 点单时，按时间线推进角色动作
# ============================================================
class OrderDirector:
    """
    导演：编排一次点单的完整动画序列。

    时间线（每步间隔由 emit_callback 控制，前端按帧渲染）：
      t=0.0s  顾客从入口走入，走向吧台
      t=1.5s  顾客到达吧台，开始点单（说气泡）
      t=2.0s  服务员走过来接待
      t=3.0s  服务员记录订单，走向咖啡师
      t=4.0s  咖啡师开始制作（工作动画）
      t=6.0s  咖啡师完成，服务员端咖啡给顾客
      t=7.5s  顾客收到咖啡，走向座位
      t=9.0s  顾客坐下喝咖啡（happy 动画）
    """

    def __init__(
        self,
        customer: Actor,
        waiter: Actor,
        barista: Actor,
        emit: Callable[[GameEvent], Any],
    ):
        self.customer = customer
        self.waiter = waiter
        self.barista = barista
        self.emit = emit  # 事件广播回调（同步或异步）

    async def _do_emit(self, event: GameEvent) -> None:
        """统一处理同步/异步 emit"""
        result = self.emit(event)
        if asyncio.iscoroutine(result):
            await result

    async def play_message_sequence(self, message: str) -> None:
        """
        用户发送消息时的角色动画序列。
        顾客走入 → 走向吧台 → 说话（气泡显示消息）
        """
        c, w = self.customer, self.waiter

        # 1. 顾客走向吧台
        counter = SCENE_POSITIONS["counter"]
        c.move_to(counter["x"] - 1, counter["y"] + 1)
        await self._do_emit(GameEvent("ACTOR_MOVE", c.actor_id, {
            "x": c.x, "y": c.y, "target_x": c.target_x, "target_y": c.target_y,
            "facing": c.facing, "animation": "walk",
        }))
        await asyncio.sleep(1.2)
        c.arrive()

        # 2. 顾客说话（气泡）
        c.state = CustomerState.ORDERING.value
        c.animation = "talk"
        c.speech_bubble = message[:40] + ("..." if len(message) > 40 else "")
        await self._do_emit(GameEvent("ACTOR_SPEAK", c.actor_id, {
            "x": c.x, "y": c.y, "animation": "talk",
            "speech_bubble": c.speech_bubble, "facing": c.facing,
        }))

    async def play_order_sequence(self, coffee_name: str, order_id: int) -> None:
        """
        订单创建成功后的完整制作动画序列。
        服务员接待 → 咖啡师制作 → 上菜 → 顾客入座
        """
        c, w, b = self.customer, self.waiter, self.barista

        # 1. 服务员走向吧台（接待顾客）
        counter = SCENE_POSITIONS["counter"]
        w.move_to(counter["x"] + 1, counter["y"] + 1)
        w.state = StaffState.TALKING.value
        await self._do_emit(GameEvent("ACTOR_MOVE", w.actor_id, {
            "x": w.x, "y": w.y, "target_x": w.target_x, "target_y": w.target_y,
            "animation": "walk", "facing": w.facing,
        }))
        await asyncio.sleep(1.0)
        w.arrive()

        # 2. 服务员说话
        w.speech_bubble = f"好的，一杯{coffee_name}！"
        w.animation = "talk"
        await self._do_emit(GameEvent("ACTOR_SPEAK", w.actor_id, {
            "x": w.x, "y": w.y, "animation": "talk",
            "speech_bubble": w.speech_bubble,
        }))
        await asyncio.sleep(1.0)

        # 3. 服务员走向咖啡师传递订单
        barista_pos = SCENE_POSITIONS["barista_station"]
        w.move_to(barista_pos["x"] - 1, barista_pos["y"])
        w.speech_bubble = None
        await self._do_emit(GameEvent("ACTOR_MOVE", w.actor_id, {
            "x": w.x, "y": w.y, "target_x": w.target_x, "target_y": w.target_y,
            "animation": "walk",
        }))
        await asyncio.sleep(1.0)
        w.arrive()

        # 4. 咖啡师开始制作
        b.state = StaffState.WORKING.value
        b.animation = "work"
        b.speech_bubble = "制作中..."
        await self._do_emit(GameEvent("ACTOR_ANIMATE", b.actor_id, {
            "x": b.x, "y": b.y, "animation": "work", "speech_bubble": "制作中...",
        }))

        # 5. 顾客等待
        c.state = CustomerState.WAITING.value
        c.animation = "idle"
        c.speech_bubble = None
        await self._do_emit(GameEvent("ACTOR_ANIMATE", c.actor_id, {
            "x": c.x, "y": c.y, "animation": "idle",
        }))
        await asyncio.sleep(2.0)

        # 6. 咖啡师完成，服务员端咖啡
        b.state = StaffState.IDLE.value
        b.animation = "idle"
        b.speech_bubble = None
        await self._do_emit(GameEvent("ACTOR_ANIMATE", b.actor_id, {
            "animation": "idle",
        }))

        # 服务员端咖啡走向顾客
        w.move_to(c.x + 1, c.y)
        w.speech_bubble = f"您的{coffee_name}好啦~"
        await self._do_emit(GameEvent("ACTOR_MOVE", w.actor_id, {
            "x": w.x, "y": w.y, "target_x": w.target_x, "target_y": w.target_y,
            "animation": "walk", "speech_bubble": w.speech_bubble,
        }))
        await asyncio.sleep(1.0)
        w.arrive()

        # 7. 顾客收到咖啡，走向座位
        seat = SCENE_POSITIONS["seat_1"]
        c.state = CustomerState.HAPPY.value
        c.move_to(seat["x"], seat["y"])
        c.speech_bubble = "谢谢！"
        await self._do_emit(GameEvent("ACTOR_MOVE", c.actor_id, {
            "x": c.x, "y": c.y, "target_x": c.target_x, "target_y": c.target_y,
            "animation": "walk", "speech_bubble": "谢谢！", "facing": c.facing,
        }))
        await asyncio.sleep(1.5)
        c.arrive()

        # 8. 顾客坐下喝咖啡
        c.state = CustomerState.EATING.value
        c.animation = "eat"
        c.speech_bubble = "真好吃！😋"
        await self._do_emit(GameEvent("ACTOR_ANIMATE", c.actor_id, {
            "x": c.x, "y": c.y, "animation": "eat", "speech_bubble": c.speech_bubble,
        }))

        # 服务员回到待命点
        waiter_home = SCENE_POSITIONS["waiter_home"]
        w.move_to(waiter_home["x"], waiter_home["y"])
        w.state = StaffState.IDLE.value
        w.speech_bubble = None
        await self._do_emit(GameEvent("ACTOR_MOVE", w.actor_id, {
            "x": w.x, "y": w.y, "target_x": w.target_x, "target_y": w.target_y,
            "animation": "walk",
        }))
