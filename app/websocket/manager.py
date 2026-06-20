# -*- coding: utf-8 -*-
"""
WebSocket 连接管理器
管理每个用户的游戏会话连接，广播角色动画事件给前端

设计：
  - 每个用户一个连接（按 user_id 索引）
  - 广播事件时，找到对应用户的连接推送
  - Skill 点单时（无网页连接），事件存入队列，用户打开页面后回放
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Dict, Optional

from fastapi import WebSocket

logger = logging.getLogger("a2a.game.ws")


class ConnectionManager:
    """WebSocket 连接管理器（单例）"""

    def __init__(self):
        # user_id -> WebSocket 连接
        self._connections: Dict[int, WebSocket] = {}
        # user_id -> 待发送事件队列（用户离线时缓存）
        self._pending: Dict[int, list] = {}

    async def connect(self, user_id: int, websocket: WebSocket) -> None:
        """接受新连接"""
        await websocket.accept()
        self._connections[user_id] = websocket
        logger.info(f"[WS] 用户 {user_id} 已连接游戏界面")

        # 回放缓存的事件
        if user_id in self._pending:
            for event in self._pending[user_id]:
                await websocket.send_text(json.dumps(event, ensure_ascii=False))
            self._pending.pop(user_id, None)

    def disconnect(self, user_id: int) -> None:
        """断开连接"""
        self._connections.pop(user_id, None)
        logger.info(f"[WS] 用户 {user_id} 断开游戏界面")

    async def send_to_user(self, user_id: int, event: dict) -> None:
        """
        给指定用户发送事件。
        如果用户在线（有 WebSocket 连接），直接推送；
        如果离线（如 Skill 点单时没开网页），缓存到队列。
        """
        ws = self._connections.get(user_id)
        if ws is not None:
            try:
                await ws.send_text(json.dumps(event, ensure_ascii=False))
            except Exception as e:
                logger.warning(f"[WS] 推送失败 user={user_id}: {e}")
                self.disconnect(user_id)
                self._pending.setdefault(user_id, []).append(event)
        else:
            # 离线缓存（最多保留 50 条，防止内存泄漏）
            queue = self._pending.setdefault(user_id, [])
            queue.append(event)
            if len(queue) > 50:
                self._pending[user_id] = queue[-50:]

    def is_online(self, user_id: int) -> bool:
        """检查用户是否在线（游戏界面是否打开）"""
        return user_id in self._connections


# 全局单例
manager = ConnectionManager()
