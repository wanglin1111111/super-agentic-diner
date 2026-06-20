# ============================================================
# 【面试题·任务三】FastAPI 入口：聊天 + 下单接口
#
# 对应面试题任务三："就买你刚才推荐的那杯，从我余额里扣钱吧。"
# 后端处理流程（把 Redis、MySQL、LLM 串联）：
#
#   第1步：读 Redis 上下文（让 AI 知道"刚才推荐的那杯"是哪杯）
#   第2步：调 LLM 理解用户意图（是下单？推荐？闲聊？）
#   第3步：解析具体咖啡名（价格 → LLM引用 → RAG关键词 → 历史提取，4路优先级）
#   第4步：显示订单摘要，存入 Redis 待确认（两段式下单，防误扣）
#   第5步：用户确认后 → MySQL 事务内扣款（BEGIN → FOR UPDATE行锁 → 校验 → 扣+插 → COMMIT）
#   第6步：写回 Redis 记忆 + 返回话术
#
# 安全提示（面试题要求）：扣钱操作用事务+行锁保证安全性和顺序
# ============================================================
from typing import Optional

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.database import get_db
from app.db.models import CoffeeKB, Order, User
from app.llm import client as llm
from app.memory.chat_history import (
    add_message,
    clear_pending_order,
    get_history,
    get_pending_order,
    set_pending_order,
)
from app.services.chat_service import extract_price, handle_message, match_by_price
from app.rag.keywords import extract_keywords
from app.rag.retrieval import retrieve
from app.services.order_service import (
    CoffeeNotResolvedError,
    InsufficientBalanceError,
    place_order,
    place_orders,
)

# ===== A2A 扩展导入 =====
from app.websocket.manager import manager as ws_manager
from app.game.actors import (
    create_customer, create_waiter, create_barista, create_cashier,
)
from app.game.director import OrderDirector, GameEvent
from app.evomap.client import evomap_client, InsufficientPointsError
from app.skill.service import (
    order_coffee_via_skill,
    get_menu_via_skill,
    check_points_via_skill,
    query_order_via_skill,
)

app = FastAPI(title="A2A 超级点单系统")

# CORS（允许前端跨域访问）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== A2A Protocol 路由 =====
from app.a2a.server import router as a2a_router
from app.a2a.server import agent_card_router
app.include_router(a2a_router)
app.include_router(agent_card_router)

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# 挂载静态资源目录（让 pixel-sprites.js 等文件可被直接访问）
app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


def _resolve_coffees_from_history(db, history, max_messages=1):
    """【任务三·第3步】从 Redis 历史消息中提取被推荐过的咖啡名。

    对应面试题：用户说"就买你刚才推荐的那杯"→ 需要从对话历史解析出具体咖啡名。

    max_messages=1：只看最近1条assistant消息（默认，避免前面聊过5杯就全选）
    max_messages=3：看最近3条（用户说"这两杯了"时用，跨多轮提取）
    """
    all_coffees = [c.coffee_name for c in db.query(CoffeeKB).all()]
    found = []
    msg_count = 0
    for msg in reversed(history):
        if msg.get("role") == "assistant":
            msg_count += 1
            if msg_count > max_messages:
                break
            for name in all_coffees:
                if name in msg.get("content", "") and name not in found:
                    found.append(name)
    return found


def _lookup_price_from_kb(db, coffee_name):
    """从知识库查价格，查不到返回 0"""
    kb = db.query(CoffeeKB).filter(CoffeeKB.coffee_name == coffee_name).first()
    return float(kb.price) if kb else 0.0


# 待确认订单时，用户确认的触发词（LLM 优先，这是关键字的底层兜底）
_CONFIRM_WORDS = (
    "确认", "下单", "好的", "好", "对", "是的", "没错",
    "买", "可以", "行", "就下单", "下单吧", "结账",
    "没错下单", "对了下单",
)


def _is_confirming(user_msg):
    """判断用户是否在确认待支付订单。
    用 startswith 而非 in，避免「不对」被「对」误判。
    """
    msg = user_msg.strip()
    if len(msg) <= 6:
        return any(msg.startswith(w) for w in _CONFIRM_WORDS)
    return False


# 修改/取消订单的信号词
_MODIFY_WORDS = (
    "只要", "不要", "换", "改", "改成", "换成",
    "去掉", "太多了", "错了", "不对", "不是",
    "来个", "来杯", "给我来", "帮我来",
)


def _is_modifying(user_msg):
    """判断用户是否在修改/重新选择待确认订单"""
    return any(w in user_msg for w in _MODIFY_WORDS)


class ChatRequest(BaseModel):
    user_id: int
    message: str
    request_id: Optional[str] = None  # 可选，下单幂等键


class ChatResponse(BaseModel):
    reply: str
    order_id: Optional[int] = None


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest, db: Session = Depends(get_db)):
    """【面试题·任务三·完整流程】聊天/下单主接口

    当用户发来 "就买你刚才推荐的那杯，从我余额里扣钱吧。" 时：
    第1步：读 Redis 上下文 → 知道"刚才推荐的那杯"是什么
    第2步：调 LLM → 理解用户意图（下单确认）
    第3步：解析咖啡名 → 价格/LLM/RAG/历史 四路优先
    第4步：显示摘要 → 存 Redis 待确认（两段式防误扣）
    第5步：用户回复"确认" → MySQL 事务扣款
    第6步：写 Redis 记忆 + 返回话术
    """
    # ===== 第1步：读 Redis 上下文（短期记忆，最近5轮对话）=====
    history = get_history(req.user_id)

    # ===== 第0步：检查 Redis 是否有待确认订单（两段式下单）=====
    pending = get_pending_order(req.user_id)

    if pending:
        # --- 有待确认订单：判断用户是确认还是修改 ---
        if _is_confirming(req.message):
            # 用户明确回复了确认词（"确认"/"下单"/"好"等）→ 执行扣款
            try:
                items = [(item["name"], req.request_id if i == 0 else None)
                         for i, item in enumerate(pending["coffees"])]
                orders = place_orders(db, req.user_id, items)
            except InsufficientBalanceError as e:
                clear_pending_order(req.user_id)
                reply = f"下单失败：{e}。请先充值哦~"
                add_message(req.user_id, "user", req.message)
                add_message(req.user_id, "assistant", reply)
                return ChatResponse(reply=reply)

            clear_pending_order(req.user_id)
            user = db.query(User).filter(User.user_id == req.user_id).first()
            balance = user.balance if user else "?"
            if len(orders) == 1:
                reply = (
                    f"好嘞！已为您下单「{orders[0].coffee_name}」，扣款 ¥{orders[0].amount}，"
                    f"当前余额 ¥{balance}。祝您品尝愉快~"
                )
            else:
                order_lines = "\n".join(f"  • {o.coffee_name} ¥{o.amount}" for o in orders)
                total = sum(o.amount for o in orders)
                reply = (
                    f"好嘞！已为您下单 {len(orders)} 杯：\n{order_lines}\n"
                    f"合计 ¥{total}，当前余额 ¥{balance}。祝您品尝愉快~"
                )
            add_message(req.user_id, "user", req.message)
            add_message(req.user_id, "assistant", reply)

            # ===== A2A 扩展：触发游戏动画 =====
            _trigger_order_animation_async(req.user_id, orders[0].coffee_name, orders[0].order_id)

            return ChatResponse(reply=reply, order_id=orders[0].order_id)
        else:
            # 不是纯确认词 → 清掉待确认，落入下方正常流程重新处理
            # （覆盖：修改订单"只要28的"、换一杯、重新选、闲聊等所有情况）
            clear_pending_order(req.user_id)

    # ===== 第2步：无待确认订单 → 调 LLM 理解用户意图 =====
    # LLM 返回三分类之一：order（下单）/ recommend（求推荐）/ chat（闲聊）
    intent = llm.parse_intent(history, req.message)

    # ===== 第3步：LLM 判断为"下单"意图 → 解析具体是哪杯咖啡 =====
    # 四路优先级：价格匹配 > LLM显式名 > RAG关键词 > 历史提取
    if intent.get("intent") == "order":
        coffees = []

        # 第3.1路：价格匹配（"来个28元的"→ 按价格查 MySQL CoffeeKB）
        price = extract_price(req.message)
        if price is not None:
            coffees = [c.coffee_name for c in match_by_price(db, price)]

        # 第3.2路：LLM 显式给出了咖啡名（它能理解"一开始说的""刚才那杯"等引用）
        # 信任 LLM 的引用理解能力，排在历史盲扫之前
        if not coffees:
            coffee = intent.get("coffee_name")
            if coffee:
                # 处理 LLM 可能返回合并名 "柑橘冷萃和美式咖啡"
                valid_names = [c.coffee_name for c in db.query(CoffeeKB).all()]
                parts = [p.strip() for p in coffee.replace("和", ",").replace("、", ",").split(",") if p.strip()]
                matched = [p for p in parts if p in valid_names]
                if matched:
                    coffees = matched

        # 第3.3路：消息含描述性词（"无牛奶"/"果味"）→ RAG 关键词过滤
        if not coffees:
            positive, negative = extract_keywords(req.message)
            if positive or negative:
                coffees = [r.coffee_name for r in retrieve(db, positive, negative)]

        # 第3.4路：以上都没命中 → 从 Redis 历史提取（最弱信号，兜底）
        # 默认只看最近1条消息（1杯）；用户说「两杯/这些/都」时跨轮提取3条
        _MULTI_SIGNALS = ("两杯", "这些", "都", "全部", "三个", "两个", "这几杯", "都来", "全要")
        wants_multi = any(w in req.message for w in _MULTI_SIGNALS)
        if not coffees:
            scan = 3 if wants_multi else 1
            coffees = _resolve_coffees_from_history(db, history, max_messages=scan)

        # 默认只取 1 杯，除非明确说了多杯信号
        if len(coffees) > 1 and not wants_multi:
            coffees = coffees[:1]

        if not coffees:
            reply = "不好意思，我没太确定您想买哪杯，能再说一下咖啡名字吗？"
            add_message(req.user_id, "user", req.message)
            add_message(req.user_id, "assistant", reply)
            return ChatResponse(reply=reply)

        # ===== 第4步：两段式下单 —— 先存 Redis 待确认，显示摘要（不直接扣款）=====
        # 把选中的咖啡名+价格存入 Redis，等用户回复"确认"后才执行扣款
        items = []
        for name in coffees:
            p = _lookup_price_from_kb(db, name)
            items.append({"name": name, "price": p})
        total = sum(i["price"] for i in items)
        set_pending_order(req.user_id, {"coffees": items, "total": total})

        lines = "\n".join(f"  • {i['name']}  ¥{i['price']:.2f}" for i in items)
        reply = (
            f"收到！您点的是：\n{lines}\n"
            f"合计 ¥{total:.2f}\n"
            f"确认下单请回复「确认」或「下单」~"
        )
        add_message(req.user_id, "user", req.message)
        add_message(req.user_id, "assistant", reply)
        return ChatResponse(reply=reply)

    # ===== 非下单意图（recommend/chat）→ 走 RAG 聊天流程 =====
    # handle_message 内部：读Redis → 关键词提取 → LIKE检索 → LLM生成 → 写Redis
    clear_pending_order(req.user_id)
    reply = handle_message(db, req.user_id, req.message)
    return ChatResponse(reply=reply)


@app.get("/user/{user_id}")
def get_user(user_id: int, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    return {
        "user_id": user.user_id,
        "nickname": user.nickname,
        "balance": float(user.balance),
        "taste_preference": user.taste_preference,
    }


@app.get("/history/{user_id}")
def get_chat_history(user_id: int):
    return get_history(user_id)


@app.delete("/history/{user_id}")
def clear_chat_history(user_id: int):
    from app.memory.chat_history import clear_history

    clear_history(user_id)
    return {"ok": True}


@app.get("/orders/{user_id}")
def list_orders(user_id: int, db: Session = Depends(get_db)):
    """返回某用户的订单列表（最近 10 单），供网页侧栏展示"""
    rows = (
        db.query(Order)
        .filter(Order.user_id == user_id)
        .order_by(Order.created_at.desc())
        .limit(10)
        .all()
    )
    return [
        {
            "order_id": o.order_id,
            "coffee_name": o.coffee_name,
            "amount": float(o.amount),
            "status": o.status,
            "created_at": o.created_at.strftime("%Y-%m-%d %H:%M"),
        }
        for o in rows
    ]


@app.get("/status")
def status():
    """返回系统状态：数据库模式、记忆模式、LLM 是否真实接入"""
    from app.config import settings

    return {
        "db_mode": settings.db_mode,
        "memory_mode": settings.memory_mode,
        "llm_active": llm.has_real_key(),
    }


@app.get("/")
def index():
    """根路由：返回像素咖啡馆游戏页面"""
    return FileResponse(_STATIC_DIR / "index.html")


# ============================================================
# ===== A2A 扩展：游戏动画触发 =====
# ============================================================
def _trigger_order_animation_async(user_id: int, coffee_name: str, order_id: int):
    """在后台线程触发游戏动画（不阻塞 HTTP 响应）"""
    import asyncio
    import threading

    async def _run():
        customer = create_customer(user_id)
        waiter = create_waiter()
        barista = create_barista()
        # spawn 角色
        for actor in [customer, waiter, barista]:
            await ws_manager.send_to_user(user_id, GameEvent(
                "ACTOR_SPAWN", actor.actor_id, actor.to_dict()
            ).to_dict())
        # 播放动画序列
        director = OrderDirector(
            customer, waiter, barista,
            lambda ev: ws_manager.send_to_user(user_id, ev.to_dict())
        )
        await director.play_order_sequence(coffee_name, order_id)

    def _thread_target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run())
        except Exception as e:
            print(f"[游戏] 动画失败（不影响订单）: {e}")
        finally:
            loop.close()

    threading.Thread(target=_thread_target, daemon=True).start()


# ============================================================
# ===== A2A 扩展：WebSocket 游戏端点 =====
# ============================================================
@app.websocket("/ws/game/{user_id}")
async def game_websocket(websocket: WebSocket, user_id: int):
    """游戏 WebSocket：实时推送角色动画事件给前端"""
    await ws_manager.connect(user_id, websocket)
    try:
        while True:
            # 接收前端消息（目前仅保活，可扩展为前端控制角色）
            data = await websocket.receive_text()
            # 可在此处理前端发来的交互指令
    except WebSocketDisconnect:
        ws_manager.disconnect(user_id)


# ============================================================
# ===== A2A 扩展：积分接口 =====
# ============================================================
@app.get("/user/{user_id}/points")
def get_points(user_id: int, db: Session = Depends(get_db)):
    """查询用户积分余额（EvoMap）"""
    user = db.query(User).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "用户不存在")
    return {
        "user_id": user_id,
        "points": user.points,
        "balance": float(user.balance),
        "nickname": user.nickname,
    }


@app.post("/user/{user_id}/points/recharge")
def recharge_points(user_id: int, amount: int, db: Session = Depends(get_db)):
    """充值积分（测试/演示用）"""
    tx = evomap_client.recharge_points(db, user_id, amount)
    return {
        "success": True,
        "points": tx.balance_after,
        "tx_id": tx.tx_id,
        "message": f"充值成功！当前积分 {tx.balance_after}",
    }


@app.get("/user/{user_id}/points/transactions")
def list_point_transactions(user_id: int, db: Session = Depends(get_db)):
    """查询积分流水（最近 20 条）"""
    from app.db.models import PointsTransaction
    rows = (
        db.query(PointsTransaction)
        .filter(PointsTransaction.user_id == user_id)
        .order_by(PointsTransaction.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "tx_id": t.tx_id,
            "delta": t.delta,
            "balance_after": t.balance_after,
            "reason": t.reason,
            "source": t.source,
            "order_id": t.order_id,
            "created_at": t.created_at.strftime("%Y-%m-%d %H:%M:%S"),
        }
        for t in rows
    ]


# ============================================================
# ===== A2A 扩展：Skill API（供 MCP / 外部 Agent 调用）=====
# ============================================================
class SkillOrderRequest(BaseModel):
    user_id: int
    coffee_name: str
    agent_type: str = "api"
    quantity: int = 1
    special_requests: str = ""


@app.post("/api/v1/skill/order")
def skill_order(req: SkillOrderRequest, db: Session = Depends(get_db)):
    """【Skill 核心 API】通过 Skill 点单（供 Agent / MCP 调用）

    流程：匹配咖啡 → 检查积分 → 扣减积分 → 创建订单 → 触发游戏动画
    """
    result = order_coffee_via_skill(
        db,
        user_id=req.user_id,
        coffee_name=req.coffee_name,
        agent_type=req.agent_type,
        quantity=req.quantity,
        special_requests=req.special_requests,
    )
    return result


@app.get("/api/v1/skill/menu")
def skill_menu(db: Session = Depends(get_db)):
    """【Skill API】获取菜单"""
    return get_menu_via_skill(db)


@app.get("/api/v1/skill/points/{user_id}")
def skill_points(user_id: int, db: Session = Depends(get_db)):
    """【Skill API】查询积分"""
    return check_points_via_skill(db, user_id)


@app.get("/api/v1/skill/order/{order_id}")
def skill_query_order(order_id: int, db: Session = Depends(get_db)):
    """【Skill API】查询订单状态"""
    return query_order_via_skill(db, order_id)


@app.get("/api/v1/status")
def a2a_status():
    """A2A 系统状态"""
    from app.config import settings
    return {
        "system": "A2A Super Order System",
        "version": "1.0.0",
        "db_mode": settings.db_mode,
        "memory_mode": settings.memory_mode,
        "llm_active": llm.has_real_key(),
        "evomap_mode": "mock" if settings.evomap_mock else "real",
        "game_enabled": settings.game_enabled,
        "mcp_enabled": settings.mcp_enabled,
        "skill_order_cost": settings.skill_order_cost,
    }
