# -*- coding: utf-8 -*-
"""
Skill 服务：处理 Agent 通过 Skill 发起的点单请求

核心功能：
  1. order_coffee_via_skill —— 通过 Skill 点单（消耗积分 + 创建订单 + 触发游戏动画）
  2. get_menu_via_skill —— 获取菜单
  3. check_points_via_skill —— 查询积分余额
  4. query_order_via_skill —— 查询订单状态

与网页点单的区别：
  - Skill 点单自动确认（无需两段式确认），因为 Agent 已明确意图
  - Skill 点单消耗积分（而非余额），对接 EvoMap
  - Skill 点单触发游戏动画（通过 WebSocket 广播，离线时缓存）
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import CoffeeKB, Order, SkillLog, User
from app.evomap.client import InsufficientPointsError, evomap_client
from app.game.actors import (
    create_customer, create_waiter, create_barista, create_cashier,
)
from app.game.director import OrderDirector, GameEvent
from app.websocket.manager import manager as ws_manager

logger = logging.getLogger("a2a.skill")


async def _emit_game_event(user_id: int, event: GameEvent) -> None:
    """把游戏事件广播给前端（异步）"""
    await ws_manager.send_to_user(user_id, event.to_dict())


async def _trigger_order_animation(
    user_id: int, coffee_name: str, order_id: int
) -> None:
    """触发点单的完整游戏动画序列"""
    try:
        customer = create_customer(user_id)
        waiter = create_waiter()
        barista = create_barista()

        # 先 spawn 角色
        for actor in [customer, waiter, barista]:
            await _emit_game_event(user_id, GameEvent(
                "ACTOR_SPAWN", actor.actor_id, actor.to_dict()
            ))

        director = OrderDirector(customer, waiter, barista, _emit_game_event)
        await director.play_order_sequence(coffee_name, order_id)
    except Exception as e:
        logger.warning(f"[游戏] 动画触发失败（不影响订单）: {e}")


def _resolve_coffee(db: Session, coffee_name: str) -> Optional[CoffeeKB]:
    """模糊匹配咖啡名"""
    # 精确匹配
    kb = db.query(CoffeeKB).filter(CoffeeKB.coffee_name == coffee_name).first()
    if kb:
        return kb
    # 模糊匹配
    kb = db.query(CoffeeKB).filter(CoffeeKB.coffee_name.like(f"%{coffee_name}%")).first()
    return kb


def _ensure_user(db: Session, user_id: int) -> User:
    """确保用户存在"""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise ValueError(f"用户不存在: {user_id}")
    return user


def order_coffee_via_skill(
    db: Session,
    user_id: int,
    coffee_name: str,
    agent_type: str = "claude",
    quantity: int = 1,
    special_requests: str = "",
) -> dict:
    """
    【Skill 核心】通过 Skill 点单。

    流程：
      1. 匹配咖啡
      2. 检查积分余额
      3. 扣减积分（EvoMap）
      4. 创建订单
      5. 记录 Skill 日志
      6. 触发游戏动画（异步）

    返回：{"order_id": ..., "coffee_name": ..., "points_cost": ..., "balance": ...}
    """
    import asyncio

    # 1. 匹配咖啡
    kb = _resolve_coffee(db, coffee_name)
    if not kb:
        return {"error": f"未找到咖啡: {coffee_name}", "available": _list_menu_names(db)}

    # 2. 计算积分消耗
    points_cost = settings.skill_order_cost * quantity

    # 3. 确保用户存在
    user = _ensure_user(db, user_id)

    # 4. 检查积分
    balance = evomap_client.get_balance(db, user_id)
    if balance < points_cost:
        return {
            "error": "积分不足",
            "needed": points_cost,
            "balance": balance,
            "hint": f"需要 {points_cost} 积分，当前余额 {balance}，请充值后再试",
        }

    # 5. 创建订单（先创建，再扣积分，失败则回滚）
    try:
        order = Order(
            user_id=user_id,
            coffee_name=kb.coffee_name,
            amount=kb.price,
            status=1,  # 已支付
        )
        db.add(order)
        db.flush()  # 拿到 order_id

        # 6. 扣减积分
        evomap_client.deduct_points(
            db, user_id, points_cost,
            reason=f"Skill点单:{kb.coffee_name} x{quantity}",
            order_id=order.order_id,
            source=f"skill:{agent_type}",
        )

        db.commit()
        db.refresh(order)

    except InsufficientPointsError as e:
        db.rollback()
        return {"error": str(e)}
    except Exception as e:
        db.rollback()
        logger.error(f"[Skill] 点单失败: {e}")
        return {"error": f"点单失败: {e}"}

    # 7. 记录 Skill 日志
    log = SkillLog(
        skill_id="order_coffee",
        user_id=user_id,
        agent_type=agent_type,
        params={"coffee_name": coffee_name, "quantity": quantity, "special_requests": special_requests},
        result={"order_id": order.order_id, "status": "success"},
        points_cost=points_cost,
        success=1,
    )
    db.add(log)
    db.commit()

    # 8. 触发游戏动画（异步，不阻塞返回）
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(_trigger_order_animation(user_id, kb.coffee_name, order.order_id))
        else:
            loop.run_until_complete(_trigger_order_animation(user_id, kb.coffee_name, order.order_id))
    except RuntimeError:
        # 没有事件循环，用新线程跑
        import threading
        def _run():
            new_loop = asyncio.new_event_loop()
            asyncio.set_event_loop(new_loop)
            new_loop.run_until_complete(_trigger_order_animation(user_id, kb.coffee_name, order.order_id))
            new_loop.close()
        threading.Thread(target=_run, daemon=True).start()

    # 9. 返回结果
    new_balance = evomap_client.get_balance(db, user_id)
    return {
        "success": True,
        "order_id": order.order_id,
        "coffee_name": kb.coffee_name,
        "amount": float(kb.price),
        "points_cost": points_cost,
        "balance_after": new_balance,
        "message": f"点单成功！已为您下单「{kb.coffee_name}」，消耗 {points_cost} 积分，剩余 {new_balance} 积分",
    }


def _list_menu_names(db: Session) -> list[str]:
    """列出所有咖啡名"""
    return [c.coffee_name for c in db.query(CoffeeKB).order_by(CoffeeKB.price).all()]


def get_menu_via_skill(db: Session) -> dict:
    """【Skill】获取菜单"""
    items = db.query(CoffeeKB).order_by(CoffeeKB.price).all()
    return {
        "menu": [
            {
                "name": c.coffee_name,
                "price": float(c.price),
                "tags": c.tags or "",
                "description": c.content[:80] + "..." if len(c.content) > 80 else c.content,
            }
            for c in items
        ],
        "points_per_order": settings.skill_order_cost,
    }


def check_points_via_skill(db: Session, user_id: int) -> dict:
    """【Skill】查询积分余额"""
    user = _ensure_user(db, user_id)
    return {
        "user_id": user_id,
        "points": user.points,
        "balance": float(user.balance),
        "nickname": user.nickname,
    }


def query_order_via_skill(db: Session, order_id: int) -> dict:
    """【Skill】查询订单状态"""
    order = db.query(Order).filter(Order.order_id == order_id).first()
    if not order:
        return {"error": f"订单不存在: {order_id}"}
    return {
        "order_id": order.order_id,
        "coffee_name": order.coffee_name,
        "amount": float(order.amount),
        "status": ["待付款", "已付款", "已退款"][order.status] if order.status < 3 else "未知",
        "created_at": order.created_at.strftime("%Y-%m-%d %H:%M:%S"),
    }
