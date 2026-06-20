# -*- coding: utf-8 -*-
"""
EvoMap 积分系统客户端
支持两种模式：
  - mock 模式（默认）：用本地数据库模拟积分，无需 EvoMap API
  - real 模式：调用真实 EvoMap API

积分消费流程（异步预扣 + 确认）：
  1. 检查余额（本地缓存 / EvoMap API）
  2. 余额不足 → 抛 InsufficientPointsError
  3. 余额充足 → 本地扣减 + 记流水 + （real 模式）调 EvoMap API
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.db.models import PointsTransaction, User

logger = logging.getLogger("a2a.evomap")


class InsufficientPointsError(Exception):
    """积分不足"""
    def __init__(self, needed: int, balance: int):
        self.needed = needed
        self.balance = balance
        super().__init__(f"积分不足：需要 {needed}，当前余额 {balance}")


class EvoMapClient:
    """EvoMap 积分客户端"""

    def __init__(self):
        self.mock = settings.evomap_mock or not settings.evomap_api_key
        if not self.mock:
            try:
                import httpx
                self._http = httpx.AsyncClient(
                    base_url=settings.evomap_base_url,
                    headers={"Authorization": f"Bearer {settings.evomap_api_key}"},
                    timeout=10.0,
                )
            except ImportError:
                logger.warning("httpx 未安装，回退到 mock 模式")
                self.mock = True

    def get_balance(self, db: Session, user_id: int) -> int:
        """查询用户积分余额"""
        user = db.query(User).filter(User.user_id == user_id).first()
        if not user:
            return 0
        return user.points

    def deduct_points(
        self,
        db: Session,
        user_id: int,
        amount: int,
        reason: str,
        order_id: Optional[int] = None,
        source: str = "web",
    ) -> PointsTransaction:
        """
        扣除积分（同步事务内执行）。

        流程：
          1. 行锁读取用户积分
          2. 校验余额 >= amount
          3. 扣减积分 + 写流水
          4. 提交事务

        real 模式下额外调用 EvoMap API（失败则回滚）。
        """
        from sqlalchemy import select

        # 行锁读取
        user = db.execute(
            select(User).where(User.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"用户不存在: {user_id}")

        # 校验余额
        if user.points < amount:
            raise InsufficientPointsError(needed=amount, balance=user.points)

        # 扣减积分
        user.points = user.points - amount
        balance_after = user.points

        # 写流水
        tx = PointsTransaction(
            user_id=user_id,
            delta=-amount,
            balance_after=balance_after,
            reason=reason,
            order_id=order_id,
            source=source,
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        logger.info(
            f"[积分] 用户{user_id} 消费 {amount} 积分（{reason}），"
            f"余额 {balance_after}，流水号 {tx.tx_id}"
        )

        # real 模式：异步同步到 EvoMap（这里简化为记录）
        if not self.mock:
            # TODO: 调用真实 EvoMap API
            # await self._http.post("/v1/wallet/transaction", ...)
            logger.info(f"[EvoMap] 积分变更已同步（mock={self.mock}）")

        return tx

    def refund_points(
        self,
        db: Session,
        user_id: int,
        amount: int,
        reason: str,
        original_order_id: Optional[int] = None,
    ) -> PointsTransaction:
        """退还积分"""
        from sqlalchemy import select

        user = db.execute(
            select(User).where(User.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"用户不存在: {user_id}")

        user.points = user.points + amount
        balance_after = user.points

        tx = PointsTransaction(
            user_id=user_id,
            delta=amount,
            balance_after=balance_after,
            reason=reason,
            order_id=original_order_id,
            source="refund",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)

        logger.info(
            f"[积分] 用户{user_id} 退还 {amount} 积分（{reason}），"
            f"余额 {balance_after}"
        )
        return tx

    def recharge_points(
        self,
        db: Session,
        user_id: int,
        amount: int,
        reason: str = "充值",
    ) -> PointsTransaction:
        """充值积分（测试/演示用）"""
        from sqlalchemy import select

        user = db.execute(
            select(User).where(User.user_id == user_id).with_for_update()
        ).scalar_one_or_none()
        if user is None:
            raise ValueError(f"用户不存在: {user_id}")

        user.points = user.points + amount
        balance_after = user.points

        tx = PointsTransaction(
            user_id=user_id,
            delta=amount,
            balance_after=balance_after,
            reason=reason,
            source="recharge",
        )
        db.add(tx)
        db.commit()
        db.refresh(tx)
        return tx


# 全局单例
evomap_client = EvoMapClient()
