# A2A 超级点单系统 - 技术方案文档

> **版本**: v1.0  
> **日期**: 2026-06-19  
> **作者**: AI Architect  

---

## 一、项目背景与目标

### 1.1 背景
基于 X 大佬的 base project（智能咖啡馆 AI 店长），将其升级为 **A2A 超级点单系统**。系统需要支持：
- **游戏化可视化**：客户点单过程以像素游戏形式展示
- **多 Agent 接入**：Claude Code、Codex、Cursor 等通过 Skill 完成点单
- **积分经济系统**：点单消耗 EvoMap 积分
- **语音输入支持**：网页版支持语音点单

### 1.2 核心目标

| 优先级 | 需求 | 说明 |
|--------|------|------|
| P0 | 游戏化点单可视化 | 用户发送文字时，游戏界面人物同步走动、互动 |
| P0 | Skill 平台 | 其他 Agent 可通过 Skill 完成点单，无需打开网页 |
| P0 | EvoMap 积分集成 | 点单消耗用户在 EvoMap 的积分 |
| P2 | 语音输入 | 网页版支持语音输入点单 |
| P3 | 全流程测试 | 验证以上所有流程 |

---

## 二、整体架构设计

### 2.1 架构全景图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           A2A 超级点单系统                                    │
├─────────────────────────────────────────────────────────────────────────────┤
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐   │
│  │   前端层      │  │   游戏层      │  │   Skill层    │  │   外部系统    │   │
│  │              │  │              │  │              │  │              │   │
│  │  React Web   │  │  Phaser.js   │  │  MCP Server  │  │  EvoMap API  │   │
│  │  管理后台     │  │  游戏可视化   │  │  A2A Adapter │  │  积分系统     │   │
│  │  语音输入     │  │  WebSocket   │  │  Skill Registry│  │              │   │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘   │
│         │                 │                 │                 │           │
│         └─────────────────┴─────────────────┴─────────────────┘           │
│                                    │                                       │
│                           ┌────────┴────────┐                             │
│                           │    API Gateway   │                             │
│                           │   (FastAPI)      │                             │
│                           └────────┬────────┘                             │
│                                    │                                       │
│         ┌──────────────────────────┼──────────────────────────┐            │
│         │                          │                          │            │
│  ┌──────┴──────┐          ┌──────┴──────┐          ┌──────┴──────┐       │
│  │   业务服务   │          │   游戏服务   │          │   积分服务   │       │
│  │              │          │              │          │              │       │
│  │  Order Svc   │          │  Game Svc    │          │  EvoMap Svc  │       │
│  │  Chat Svc    │          │  Scene Svc   │          │  Wallet Svc  │       │
│  │  RAG Svc     │          │  Actor Svc   │          │  Ledger Svc  │       │
│  └──────┬──────┘          └──────┬──────┘          └──────┬──────┘       │
│         │                          │                          │            │
│         └──────────────────────────┼──────────────────────────┘            │
│                                    │                                       │
│                           ┌────────┴────────┐                             │
│                           │   数据层         │                             │
│                           │  SQLite/MySQL   │                             │
│                           │  Redis/fake     │                             │
│                           └─────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 技术栈选型

| 层级 | 技术选型 | 说明 |
|------|----------|------|
| **前端** | React 19 + TypeScript | 管理后台、配置界面 |
| **游戏引擎** | Phaser 3 (Canvas/WebGL) | 2D 像素游戏可视化 |
| **实时通信** | WebSocket (原生) | 游戏状态同步 |
| **后端** | FastAPI + Python 3.10+ | API 服务 |
| **数据库** | SQLite (开发) / MySQL (生产) | 业务数据 |
| **缓存** | Redis / fakeredis | 会话、游戏状态 |
| **Skill 协议** | MCP (Model Context Protocol) | Agent 接入标准 |
| **A2A 协议** | Google A2A Protocol | 跨 Agent 协作 |
| **语音** | Web Speech API | 浏览器原生语音输入 |

---

## 三、核心模块设计

### 3.1 游戏可视化模块 (P0)

#### 3.1.1 游戏场景设计

```
┌─────────────────────────────────────────┐
│           咖啡馆场景 (64x64 网格)          │
│                                         │
│  ┌─────┐    ┌─────┐    ┌─────┐         │
│  │收银台│    │菜单板│    │咖啡机│         │
│  │     │    │     │    │     │         │
│  └──┬──┘    └─────┘    └──┬──┘         │
│     │                      │            │
│  ┌──┴──┐              ┌───┴───┐        │
│  │ 顾客 │───走动──────→│ 服务员 │        │
│  │入口 │              │      │        │
│  └──┬──┘              └───┬───┘        │
│     │                      │            │
│     └──────────┬───────────┘            │
│                ↓                        │
│           ┌─────────┐                   │
│           │  座位区  │                   │
│           │ ○ ○ ○ ○ │                   │
│           └─────────┘                   │
│                                         │
└─────────────────────────────────────────┘
```

#### 3.1.2 角色系统

| 角色 | 类型 | 行为 | 动画 |
|------|------|------|------|
| **顾客** | 用户代理 | 走入 → 看菜单 → 点单 → 等待 → 取餐 → 离开 | idle, walk, read, talk, eat, happy |
| **服务员** | 系统代理 | 接待 → 记录订单 → 传递后厨 → 送餐 | idle, walk, talk, write, serve |
| **咖啡师** | 系统代理 | 接收订单 → 制作咖啡 → 出餐 | idle, work, talk, serve |
| **收银员** | 系统代理 | 结算 → 扣积分 → 打小票 | idle, talk, operate |

#### 3.1.3 状态机设计

```typescript
// 顾客状态机
interface CustomerState {
  state: 'entering' | 'walking' | 'reading_menu' | 'ordering' | 
         'waiting' | 'receiving' | 'eating' | 'leaving';
  position: { x: number; y: number };
  target: { x: number; y: number } | null;
  order: Order | null;
}

// 状态转换触发器
type StateTrigger = 
  | { type: 'USER_MESSAGE'; message: string }      // 用户发送消息
  | { type: 'ORDER_CREATED'; orderId: string }     // 订单创建
  | { type: 'ORDER_CONFIRMED'; orderId: string }   // 订单确认
  | { type: 'PAYMENT_SUCCESS'; orderId: string }   // 支付成功
  | { type: 'DRINK_READY'; orderId: string }       // 饮品完成
  | { type: 'SKILL_ORDER'; skillId: string };      // Skill 点单
```

#### 3.1.4 WebSocket 事件协议

```typescript
// 客户端 → 服务器
interface ClientEvent {
  type: 'JOIN' | 'MESSAGE' | 'ORDER' | 'VOICE' | 'SKILL_REQUEST';
  payload: unknown;
}

// 服务器 → 客户端 (广播)
interface ServerEvent {
  type: 'ACTOR_SPAWN' | 'ACTOR_MOVE' | 'ACTOR_ANIMATE' | 
        'SCENE_UPDATE' | 'ORDER_UPDATE' | 'PAYMENT_RESULT';
  payload: {
    actorId: string;
    action: string;
    position?: { x: number; y: number };
    animation?: string;
    timestamp: number;
  };
}
```

### 3.2 Skill 平台模块 (P0)

#### 3.2.1 MCP Server 设计

```python
# app/mcp/server.py
from mcp.server import Server
from mcp.types import Tool, TextContent

class OrderSkillServer:
    """MCP Server for A2A Super Order System"""
    
    def __init__(self):
        self.server = Server("a2a-super-order")
        self._register_tools()
    
    def _register_tools(self):
        """注册 Skill 工具"""
        
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="order_coffee",
                    description="Order a coffee from the A2A Super Order System",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string", "description": "User ID"},
                            "coffee_name": {"type": "string", "description": "Coffee name or description"},
                            "quantity": {"type": "integer", "default": 1},
                            "special_requests": {"type": "string", "default": ""}
                        },
                        "required": ["user_id", "coffee_name"]
                    }
                ),
                Tool(
                    name="check_balance",
                    description="Check user's EvoMap balance",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "user_id": {"type": "string", "description": "User ID"}
                        },
                        "required": ["user_id"]
                    }
                ),
                Tool(
                    name="get_menu",
                    description="Get available coffee menu",
                    inputSchema={
                        "type": "object",
                        "properties": {}
                    }
                ),
                Tool(
                    name="query_order_status",
                    description="Query order status by order ID",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "order_id": {"type": "string", "description": "Order ID"}
                        },
                        "required": ["order_id"]
                    }
                )
            ]
        
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            """处理 Skill 调用"""
            if name == "order_coffee":
                return await self._handle_order(arguments)
            elif name == "check_balance":
                return await self._handle_check_balance(arguments)
            elif name == "get_menu":
                return await self._handle_get_menu()
            elif name == "query_order_status":
                return await self._handle_query_order(arguments)
            else:
                raise ValueError(f"Unknown tool: {name}")
```

#### 3.2.2 A2A Protocol 适配器

```python
# app/a2a/adapter.py
from a2a import AgentCard, Task, Message

class A2AOrderAdapter:
    """Google A2A Protocol Adapter"""
    
    def __init__(self, base_url: str):
        self.base_url = base_url
    
    def get_agent_card(self) -> AgentCard:
        """返回 Agent 能力卡片"""
        return AgentCard(
            name="A2A Super Order Agent",
            description="AI-powered coffee ordering system with game visualization",
            url=self.base_url,
            version="1.0.0",
            capabilities={
                "streaming": True,
                "pushNotifications": False
            },
            skills=[
                {
                    "id": "order-coffee",
                    "name": "Order Coffee",
                    "description": "Order coffee with natural language",
                    "tags": ["coffee", "order", "food"]
                },
                {
                    "id": "check-balance", 
                    "name": "Check Balance",
                    "description": "Check EvoMap balance",
                    "tags": ["balance", "wallet", "evomap"]
                }
            ]
        )
    
    async def handle_task(self, task: Task) -> Task:
        """处理 A2A Task"""
        # 解析用户意图
        intent = self._parse_intent(task.message)
        
        # 执行对应操作
        if intent == "order":
            result = await self._process_order(task)
        elif intent == "query_balance":
            result = await self._query_balance(task)
        else:
            result = {"error": "Unknown intent"}
        
        # 更新任务状态
        task.status = TaskStatus.COMPLETED
        task.artifacts = [result]
        return task
```

#### 3.2.3 Skill 注册中心

```python
# app/skill/registry.py
from typing import Dict, List, Callable
from dataclasses import dataclass

@dataclass
class Skill:
    id: str
    name: str
    description: str
    handler: Callable
    required_permissions: List[str]
    cost_in_points: int  # 消耗积分

class SkillRegistry:
    """Skill 注册中心"""
    
    def __init__(self):
        self._skills: Dict[str, Skill] = {}
    
    def register(self, skill: Skill):
        """注册 Skill"""
        self._skills[skill.id] = skill
    
    def get(self, skill_id: str) -> Skill | None:
        """获取 Skill"""
        return self._skills.get(skill_id)
    
    def list_all(self) -> List[Skill]:
        """列出所有 Skill"""
        return list(self._skills.values())
    
    async def execute(self, skill_id: str, user_id: str, params: dict) -> dict:
        """执行 Skill"""
        skill = self.get(skill_id)
        if not skill:
            raise ValueError(f"Skill not found: {skill_id}")
        
        # 检查积分余额
        balance = await self._check_balance(user_id)
        if balance < skill.cost_in_points:
            raise InsufficientBalanceError(
                f"Need {skill.cost_in_points} points, but only have {balance}"
            )
        
        # 扣除积分
        await self._deduct_points(user_id, skill.cost_in_points)
        
        # 执行 Skill
        result = await skill.handler(**params)
        
        # 触发游戏可视化
        await self._emit_game_event(user_id, skill_id, result)
        
        return result
```

### 3.3 EvoMap 积分集成 (P0)

#### 3.3.1 积分服务接口

```python
# app/evomap/client.py
import httpx
from typing import Optional

class EvoMapClient:
    """EvoMap 积分系统客户端"""
    
    def __init__(self, api_key: str, base_url: str = "https://api.evomap.ai"):
        self.api_key = api_key
        self.base_url = base_url
        self.client = httpx.AsyncClient(
            headers={"Authorization": f"Bearer {api_key}"}
        )
    
    async def get_balance(self, user_id: str) -> dict:
        """查询用户积分余额"""
        response = await self.client.get(
            f"{self.base_url}/v1/wallet/{user_id}/balance"
        )
        response.raise_for_status()
        return response.json()
    
    async def deduct_points(
        self, 
        user_id: str, 
        amount: int, 
        reason: str,
        order_id: Optional[str] = None
    ) -> dict:
        """扣除积分"""
        payload = {
            "user_id": user_id,
            "amount": amount,
            "reason": reason,
            "order_id": order_id,
            "type": "deduct"
        }
        response = await self.client.post(
            f"{self.base_url}/v1/wallet/transaction",
            json=payload
        )
        response.raise_for_status()
        return response.json()
    
    async def refund_points(
        self,
        user_id: str,
        amount: int,
        reason: str,
        original_order_id: str
    ) -> dict:
        """退还积分"""
        payload = {
            "user_id": user_id,
            "amount": amount,
            "reason": reason,
            "original_order_id": original_order_id,
            "type": "refund"
        }
        response = await self.client.post(
            f"{self.base_url}/v1/wallet/transaction",
            json=payload
        )
        response.raise_for_status()
        return response.json()
```

#### 3.3.2 积分消费流程

```
┌─────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  用户    │     │  A2A 点单系统 │     │  EvoMap API │     │   游戏界面   │
└────┬────┘     └──────┬──────┘     └──────┬──────┘     └──────┬──────┘
     │                 │                   │                   │
     │  1. 发起点单      │                   │                   │
     │────────────────→│                   │                   │
     │                 │                   │                   │
     │                 │  2. 查询余额       │                   │
     │                 │────────────────→│                   │
     │                 │                   │                   │
     │                 │  3. 返回余额       │                   │
     │                 │←────────────────│                   │
     │                 │                   │                   │
     │                 │  4. 余额充足？      │                   │
     │                 │  ├─ 不足 → 返回错误  │                   │
     │                 │  └─ 充足 → 继续     │                   │
     │                 │                   │                   │
     │                 │  5. 扣除积分        │                   │
     │                 │────────────────→│                   │
     │                 │                   │                   │
     │                 │  6. 扣款成功        │                   │
     │                 │←────────────────│                   │
     │                 │                   │                   │
     │                 │  7. 创建订单        │                   │
     │                 │────────────────────────────────────→│
     │                 │                   │                   │
     │                 │                   │                   │  8. 角色动画
     │                 │                   │                   │  (顾客走动、点单)
     │                 │                   │                   │
     │  9. 返回结果      │                   │                   │
     │←────────────────│                   │                   │
     │                 │                   │                   │
```

### 3.4 语音输入模块 (P2)

#### 3.4.1 Web Speech API 集成

```typescript
// frontend/src/hooks/useVoiceInput.ts
import { useState, useCallback, useRef } from 'react';

interface VoiceInputState {
  isListening: boolean;
  transcript: string;
  error: string | null;
}

export function useVoiceInput() {
  const [state, setState] = useState<VoiceInputState>({
    isListening: false,
    transcript: '',
    error: null
  });
  
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  
  const startListening = useCallback(() => {
    if (!('webkitSpeechRecognition' in window)) {
      setState(prev => ({ ...prev, error: 'Browser does not support voice input' }));
      return;
    }
    
    const recognition = new webkitSpeechRecognition();
    recognition.lang = 'zh-CN'; // 支持中文
    recognition.continuous = false;
    recognition.interimResults = true;
    
    recognition.onstart = () => {
      setState(prev => ({ ...prev, isListening: true, error: null }));
    };
    
    recognition.onresult = (event) => {
      const transcript = Array.from(event.results)
        .map(result => result[0].transcript)
        .join('');
      setState(prev => ({ ...prev, transcript }));
    };
    
    recognition.onerror = (event) => {
      setState(prev => ({ 
        ...prev, 
        isListening: false, 
        error: event.error 
      }));
    };
    
    recognition.onend = () => {
      setState(prev => ({ ...prev, isListening: false }));
    };
    
    recognition.start();
    recognitionRef.current = recognition;
  }, []);
  
  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
  }, []);
  
  return {
    ...state,
    startListening,
    stopListening
  };
}
```

---

## 四、数据模型设计

### 4.1 核心实体

```python
# app/db/models.py

from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime

class User(Base):
    """用户表"""
    __tablename__ = "users"
    
    user_id = Column(Integer, primary_key=True)
    evomap_user_id = Column(String(64), unique=True, index=True)  # EvoMap 用户ID
    nickname = Column(String(64))
    balance = Column(Float, default=0.0)  # 本地缓存余额
    created_at = Column(DateTime, default=datetime.utcnow)

class Order(Base):
    """订单表"""
    __tablename__ = "orders"
    
    order_id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    evomap_tx_id = Column(String(128))  # EvoMap 交易ID
    coffee_name = Column(String(64))
    amount = Column(Float)
    points_cost = Column(Integer)  # 消耗积分
    status = Column(String(32), default="pending")  # pending/confirmed/completed/cancelled
    skill_id = Column(String(64), nullable=True)  # 通过哪个 Skill 下单
    agent_type = Column(String(32), default="web")  # web/skill/claude/codex
    created_at = Column(DateTime, default=datetime.utcnow)
    
    user = relationship("User", back_populates="orders")

class GameSession(Base):
    """游戏会话表"""
    __tablename__ = "game_sessions"
    
    session_id = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.user_id"))
    scene_data = Column(JSON)  # 场景状态
    actors = Column(JSON)  # 角色状态
    events = Column(JSON)  # 事件日志
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow)

class SkillLog(Base):
    """Skill 调用日志"""
    __tablename__ = "skill_logs"
    
    log_id = Column(Integer, primary_key=True)
    skill_id = Column(String(64))
    user_id = Column(Integer, ForeignKey("users.user_id"))
    params = Column(JSON)
    result = Column(JSON)
    points_cost = Column(Integer)
    success = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
```

---

## 五、API 设计

### 5.1 REST API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/` | 游戏可视化页面 |
| GET | `/admin` | 管理后台 |
| POST | `/api/v1/chat` | 聊天/点单接口 |
| GET | `/api/v1/menu` | 获取菜单 |
| GET | `/api/v1/user/{id}` | 查询用户信息 |
| GET | `/api/v1/user/{id}/balance` | 查询积分余额 |
| GET | `/api/v1/user/{id}/orders` | 查询订单历史 |
| POST | `/api/v1/order` | 创建订单 |
| POST | `/api/v1/order/{id}/confirm` | 确认订单 |
| POST | `/api/v1/order/{id}/cancel` | 取消订单 |
| GET | `/api/v1/game/session/{id}` | 获取游戏会话状态 |
| POST | `/api/v1/game/actor/{id}/move` | 移动角色 |
| GET | `/api/v1/status` | 系统状态 |

### 5.2 WebSocket API

```typescript
// 连接: ws://localhost:8000/ws/game/{session_id}

// 客户端发送
interface WSClientMessage {
  type: 'JOIN' | 'MESSAGE' | 'ORDER' | 'VOICE' | 'INTERACT';
  payload: any;
}

// 服务器广播
interface WSServerMessage {
  type: 'ACTOR_SPAWN' | 'ACTOR_MOVE' | 'ACTOR_ANIMATE' | 
        'ACTOR_SPEAK' | 'SCENE_UPDATE' | 'ORDER_UPDATE' |
        'PAYMENT_RESULT' | 'SYSTEM_MESSAGE';
  payload: {
    timestamp: number;
    [key: string]: any;
  };
}
```

---

## 六、文件结构规划

```
super-agentic-diner/
├── app/
│   ├── __init__.py
│   ├── main.py                    # FastAPI 入口
│   ├── config.py                  # 配置管理
│   ├── websocket/
│   │   ├── __init__.py
│   │   ├── manager.py             # WebSocket 连接管理
│   │   └── game_handler.py        # 游戏事件处理
│   ├── api/
│   │   ├── __init__.py
│   │   ├── chat.py                # 聊天接口
│   │   ├── order.py               # 订单接口
│   │   ├── user.py                # 用户接口
│   │   └── game.py                # 游戏接口
│   ├── mcp/
│   │   ├── __init__.py
│   │   ├── server.py              # MCP Server
│   │   └── tools.py               # MCP Tools 定义
│   ├── a2a/
│   │   ├── __init__.py
│   │   ├── adapter.py             # A2A 协议适配器
│   │   └── agent_card.py          # Agent 能力卡片
│   ├── skill/
│   │   ├── __init__.py
│   │   ├── registry.py            # Skill 注册中心
│   │   ├── executor.py            # Skill 执行器
│   │   └── skills/
│   │       ├── order_coffee.py    # 点单 Skill
│   │       ├── check_balance.py   # 查余额 Skill
│   │       └── get_menu.py        # 菜单 Skill
│   ├── evomap/
│   │   ├── __init__.py
│   │   ├── client.py              # EvoMap API 客户端
│   │   └── webhook.py             # EvoMap 回调处理
│   ├── game/
│   │   ├── __init__.py
│   │   ├── engine.py              # 游戏引擎接口
│   │   ├── scene.py               # 场景管理
│   │   ├── actor.py               # 角色管理
│   │   ├── pathfinding.py         # 路径寻找
│   │   └── events.py              # 游戏事件
│   ├── services/
│   │   ├── __init__.py
│   │   ├── chat_service.py        # 聊天服务
│   │   ├── order_service.py       # 订单服务
│   │   ├── game_service.py        # 游戏服务
│   │   └── evomap_service.py      # 积分服务
│   ├── db/
│   │   ├── __init__.py
│   │   ├── database.py            # 数据库连接
│   │   └── models.py              # 数据模型
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── keywords.py            # 关键词提取
│   │   └── retrieval.py           # 知识检索
│   ├── llm/
│   │   ├── __init__.py
│   │   └── client.py              # LLM 客户端
│   └── static/
│       ├── index.html             # 游戏页面
│       └── admin.html             # 管理后台
├── frontend/                      # React 前端 (可选)
│   ├── src/
│   │   ├── components/
│   │   ├── hooks/
│   │   └── pages/
│   └── package.json
├── game-assets/                   # 游戏资源
│   ├── characters/                # 角色精灵图
│   ├── furniture/                 # 家具
│   ├── floors/                    # 地板
│   └── walls/                     # 墙壁
├── tests/
│   ├── unit/
│   ├── integration/
│   └── e2e/── scripts/
│   └── init_db.py
├── docs/
│   └── A2A-Super-Order-System-Architecture.md
├── .env
├── requirements.txt
└── README.md
```

---

## 七、实施路线图

### Phase 1: 基础架构 (Week 1)
- [ ] 搭建 FastAPI + WebSocket 基础框架
- [ ] 设计并实现数据库模型
- [ ] 集成 EvoMap 积分客户端
- [ ] 完成基础 API 接口

### Phase 2: 游戏可视化 (Week 2)
- [ ] 集成 Phaser.js 游戏引擎
- [ ] 实现咖啡馆场景和角色系统
- [ ] 实现 WebSocket 实时同步
- [ ] 用户消息触发角色动画

### Phase 3: Skill 平台 (Week 3)
- [ ] 实现 MCP Server
- [ ] 实现 A2A Protocol 适配器
- [ ] 设计 Skill 注册中心
- [ ] 完成 Skill 调用链路

### Phase 4: 积分与支付 (Week 3-4)
- [ ] 完成 EvoMap 积分查询/扣除
- [ ] 实现订单积分消费流程
- [ ] 积分不足处理逻辑
- [ ] 交易回滚机制

### Phase 5: 语音输入 (Week 4)
- [ ] 集成 Web Speech API
- [ ] 语音转文字处理
- [ ] 语音点单流程

### Phase 6: 测试与优化 (Week 5)
- [ ] 单元测试覆盖
- [ ] 集成测试
- [ ] E2E 测试
- [ ] 性能优化

---

## 八、关键技术决策

### 8.1 为什么选择 Phaser.js 而不是自研 Canvas？

| 方案 | 优点 | 缺点 |
|------|------|------|
| **Phaser.js** | 成熟的 2D 游戏框架，内置物理、动画、路径寻找 | 包体积较大 (~500KB) |
| **自研 Canvas** | 轻量、完全可控 | 需自行实现动画、碰撞检测、路径寻找 |

**决策**：选择 Phaser.js，因为项目需要复杂的角色动画和路径寻找，自研成本过高。

### 8.2 为什么选择 MCP + A2A 双协议？

| 协议 | 适用场景 | 优势 |
|------|----------|------|
| **MCP** | Claude Code、Cursor 等 IDE 内 Agent | 生态成熟，Anthropic 主推 |
| **A2A** | 跨平台 Agent 协作 | Google 标准，支持 Agent 发现 |

**决策**：同时支持两种协议，覆盖最大范围的 Agent 接入。

### 8.3 为什么 EvoMap 积分采用异步扣款？

```
同步扣款: 用户请求 → 调用 EvoMap API → 等待响应 → 返回结果
           └─ 延迟高，API 故障时阻塞整个流程

异步扣款: 用户请求 → 本地预扣 → 异步确认 → 失败时回滚
           └─ 响应快，容错性强
```

**决策**：采用异步扣款 + 本地预扣机制，保证用户体验。

---

## 九、风险与应对

| 风险 | 影响 | 应对措施 |
|------|------|----------|
| EvoMap API 不稳定 | 积分功能不可用 | 本地缓存余额，异步同步 |
| WebSocket 并发高 | 服务器压力大 | 使用 Redis 广播，水平扩展 |
| Phaser.js 性能问题 | 游戏卡顿 | 优化渲染，限制同屏角色数 |
| MCP/A2A 协议变更 | Skill 不兼容 | 封装适配层，隔离协议细节 |
| 语音识别不准确 | 点单错误 | 提供确认环节，支持文字修正 |

---

## 十、参考资源

- [Google A2A Protocol](https://github.com/google/A2A)
- [Anthropic MCP](https://github.com/anthropics/anthropic-cookbook/tree/main/mcp)
- [Phaser.js 官方文档](https://phaser.io/docs/)
- [FastAPI WebSocket](https://fastapi.tiangolo.com/advanced/websocket/)
- [Web Speech API](https://developer.mozilla.org/en-US/docs/Web/API/Web_Speech_API)

---

> **下一步**: 确认方案后，可以开始 Phase 1 的实施。需要我输出具体的代码实现吗？
