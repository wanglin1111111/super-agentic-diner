# -*- coding: utf-8 -*-
"""
游戏角色系统
定义像素咖啡馆里的角色类型、状态机和坐标系统
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


# ============================================================
# 角色类型枚举
# ============================================================
class ActorType(str, Enum):
    CUSTOMER = "customer"      # 顾客（代表用户）
    WAITER = "waiter"          # 服务员（接待、记录订单）
    BARISTA = "barista"        # 咖啡师（制作咖啡）
    CASHIER = "cashier"        # 收银员（结算积分）


# ============================================================
# 顾客状态机
# entering → walking → reading_menu → ordering → waiting
#         → receiving → eating → leaving
# ============================================================
class CustomerState(str, Enum):
    ENTERING = "entering"           # 走进咖啡馆
    WALKING = "walking"             # 正在走向目标位置
    READING_MENU = "reading_menu"   # 看菜单
    ORDERING = "ordering"           # 正在点单
    WAITING = "waiting"             # 等待制作完成
    RECEIVING = "receiving"         # 接收饮品
    EATING = "eating"               # 喝咖啡
    LEAVING = "leaving"             # 离开
    HAPPY = "happy"                 # 满意（收到饮品后）


# 服务员/咖啡师/收银员的工作状态
class StaffState(str, Enum):
    IDLE = "idle"           # 待命
    WORKING = "working"     # 工作中
    TALKING = "talking"     # 说话中
    SERVING = "serving"     # 上菜/出餐


# ============================================================
# 场景中的关键位置（像素坐标，基于 tile_size=32 的网格）
# 这些是角色会走到的"目的地"
# ============================================================
SCENE_POSITIONS = {
    "entrance":      {"x": 1, "y": 8},     # 入口（顾客出生点）
    "menu_board":    {"x": 8, "y": 2},     # 菜单板（看菜单）
    "counter":       {"x": 12, "y": 5},    # 吧台（点单处）
    "barista_station": {"x": 16, "y": 3},  # 咖啡机（咖啡师位置）
    "cashier":       {"x": 5, "y": 5},     # 收银台
    "seat_1":        {"x": 6, "y": 12},    # 座位1
    "seat_2":        {"x": 10, "y": 12},   # 座位2
    "seat_3":        {"x": 14, "y": 12},   # 座位3
    "waiter_home":   {"x": 11, "y": 6},    # 服务员待命点
}


# ============================================================
# 角色数据结构
# ============================================================
@dataclass
class Actor:
    """一个游戏角色"""
    actor_id: str                                  # 角色唯一ID
    actor_type: str                                # 角色类型（ActorType 的值）
    name: str                                      # 角色名
    x: int                                         # 当前 x 坐标（网格单位）
    y: int                                         # 当前 y 坐标（网格单位）
    state: str = "idle"                            # 当前状态
    animation: str = "idle"                        # 当前动画
    facing: str = "down"                           # 朝向（up/down/left/right）
    target_x: Optional[int] = None                 # 目标 x（走到这里）
    target_y: Optional[int] = None                 # 目标 y
    speech_bubble: Optional[str] = None            # 气泡文字（说话内容）
    metadata: dict = field(default_factory=dict)   # 额外数据（如关联的 user_id / order_id）

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "Actor":
        return Actor(**data)

    def move_to(self, x: int, y: int) -> None:
        """设置移动目标，并计算朝向"""
        self.target_x = x
        self.target_y = y
        self.state = "walking"
        self.animation = "walk"
        # 计算朝向
        dx = x - self.x
        dy = y - self.y
        if abs(dx) > abs(dy):
            self.facing = "right" if dx > 0 else "left"
        elif dy != 0:
            self.facing = "down" if dy > 0 else "up"

    def arrive(self) -> None:
        """到达目标位置"""
        if self.target_x is not None:
            self.x = self.target_x
        if self.target_y is not None:
            self.y = self.target_y
        self.target_x = None
        self.target_y = None
        self.animation = "idle"


# ============================================================
# 角色工厂：创建各种角色
# ============================================================
def create_customer(user_id: int, nickname: str = "") -> Actor:
    """创建顾客角色（代表用户进入咖啡馆）"""
    return Actor(
        actor_id=f"customer_{user_id}",
        actor_type=ActorType.CUSTOMER.value,
        name=nickname or f"顾客{user_id}",
        x=SCENE_POSITIONS["entrance"]["x"],
        y=SCENE_POSITIONS["entrance"]["y"],
        state=CustomerState.ENTERING.value,
        animation="walk",
        facing="right",
        metadata={"user_id": user_id},
    )


def create_waiter() -> Actor:
    """创建服务员角色"""
    pos = SCENE_POSITIONS["waiter_home"]
    return Actor(
        actor_id="waiter_1",
        actor_type=ActorType.WAITER.value,
        name="服务员小美",
        x=pos["x"], y=pos["y"],
        state=StaffState.IDLE.value,
        facing="down",
    )


def create_barista() -> Actor:
    """创建咖啡师角色"""
    pos = SCENE_POSITIONS["barista_station"]
    return Actor(
        actor_id="barista_1",
        actor_type=ActorType.BARISTA.value,
        name="咖啡师老王",
        x=pos["x"], y=pos["y"],
        state=StaffState.IDLE.value,
        facing="down",
    )


def create_cashier() -> Actor:
    """创建收银员角色"""
    pos = SCENE_POSITIONS["cashier"]
    return Actor(
        actor_id="cashier_1",
        actor_type=ActorType.CASHIER.value,
        name="收银员小张",
        x=pos["x"], y=pos["y"],
        state=StaffState.IDLE.value,
        facing="down",
    )


def gen_session_id() -> str:
    """生成游戏会话ID"""
    return uuid.uuid4().hex[:16]
