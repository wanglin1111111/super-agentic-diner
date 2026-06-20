from datetime import datetime      # datetime：标准库里的时间工具，用来记"现在几点"
from decimal import Decimal        # Decimal：标准库里的精确小数，存钱用它不会算错

from sqlalchemy import (           # sqlalchemy：Python 里管数据库的库（ORM），用 Python 类来当数据库表
    BigInteger,                    # BigInteger：大整数，对应 MySQL 的 BIGINT，能存很大的编号
    Column,                        # Column：声明"这一列是字段"的函数，每个字段都得用它包一下
    DateTime,                      # DateTime：日期时间类型，对应 MySQL 的 DATETIME
    ForeignKey,                    # ForeignKey：外键，用来"指向"另一张表的某列，把两张表连起来
    Index,                         # Index：索引，给某列或某几列建个"目录"，查得更快
    Integer,                       # Integer：普通整数，对应 MySQL 的 INT
    String,                        # String：变长字符串，对应 MySQL 的 VARCHAR(n)，括号里写最大字数
    DECIMAL,                       # DECIMAL：精确小数，对应 MySQL 的 DECIMAL(10,2)（总共10位、小数2位）
    SmallInteger,                  # SmallInteger：小整数，对应 MySQL 的 SMALLINT，省地方，存那种就几个值的字段
    Text,                          # Text：长文本，对应 MySQL 的 TEXT，存一大段话
    JSON,                          # JSON：JSON 类型，存游戏场景/角色状态等结构化数据
)

from app.db.database import Base   # Base：所有表类的"地基"，定义的类都得继承它，库才知道这是个表

# 主键（primary key）= 每条记录的唯一编号，靠它能精准找到某一行。
# 真正上线用 MySQL，主键用大整数 BIGINT；
# 本地测试用 SQLite，主键得换成普通的 INTEGER 才能自增，
# 否则会报 "NOT NULL 约束失败" 的错。
# with_variant 的意思是："默认用 BigInteger，但碰到 sqlite 就改用 Integer"。
_PK = BigInteger().with_variant(Integer, "sqlite")


# ============================================================
# 第一张表：用户表（User）
# 记录每个用户：谁、还有多少钱、爱喝什么口味（比如"不加糖"）
# ============================================================
class User(Base):                  # 继承 Base，告诉 sqlalchemy："这是张表"
    """用户表：余额、口味偏好"""

    __tablename__ = "user"        # __tablename__：这张表在数据库里实际叫什么名字（表名）

    user_id = Column(_PK, primary_key=True, autoincrement=True)          # 用户编号。primary_key=True 表示它是主键；autoincrement=True 表示每来一个新用户，编号自动+1
    nickname = Column(String(64), nullable=True)                         # 昵称。String(64) 表示最多 64 个字；nullable=True 表示这列可以留空（不填也行）
    balance = Column(DECIMAL(10, 2), nullable=False, default=Decimal("0.00"))  # 账上还剩多少钱。DECIMAL(10,2) 表示最多10位、小数2位（即最多 99999999.99）；nullable=False 表示必须填；default 表示新用户默认 0 元。金额一律用 DECIMAL，不能用 float，因为 float 存钱会算错（0.1+0.2≠0.3）
    taste_preference = Column(String(255), nullable=True)                # 口味喜好，比如 "不加糖"，可以不填

    # ===== A2A 扩展字段 =====
    evomap_user_id = Column(String(128), nullable=True, index=True)     # EvoMap 平台用户ID，用于积分对接
    points = Column(Integer, nullable=False, default=100)               # 积分余额（EvoMap 积分缓存，默认赠送 100 积分）
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # 这条记录啥时候建的。default=datetime.utcnow 表示没手动指定时，自动填当前时间
    updated_at = Column(
        DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow  # 啥时候改过。onupdate=datetime.utcnow 表示每次改动这条记录，这列会自动刷新成当前时间
    )


# ============================================================
# 第二张表：订单表（Order）
# 记下：谁、买了哪杯咖啡、花了多少钱
# ============================================================
class Order(Base):
    """订单表：谁买了什么、花了多少"""

    __tablename__ = "order"

    order_id = Column(_PK, primary_key=True, autoincrement=True)          # 订单编号，自动往上加
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False)  # 是哪个用户下的单。ForeignKey("user.user_id") 表示这列"指向"用户表的 user_id 列，靠它把订单和用户连起来
    coffee_name = Column(String(128), nullable=False)                     # 买的咖啡叫啥名
    amount = Column(DECIMAL(10, 2), nullable=False)                       # 这单花了多少钱（同样用 DECIMAL 防误差）
    status = Column(SmallInteger, nullable=False, default=0)              # 订单状态：0 待付款、1 已付款、2 已退款。用 SmallInteger 是因为状态就那么几个值，犯不上用大整数
    request_id = Column(String(64), nullable=True, unique=True)          # 防重单的标记。unique=True 表示这列的值不能重复——同一个标记只能下一单，点快了也不会重复扣钱（这叫"幂等"）
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # 这单是啥时候下的

    # 给 user_id + created_at 建了个联合索引（就是把两列合在一起当目录），
    # 这样查"某人最近的订单"会很快（按用户筛、按时间倒序排）
    # __table_args__ 是放表级设置的地方（比如索引）
    __table_args__ = (Index("idx_user_created", "user_id", "created_at"),)


# ============================================================
# 第三张表：咖啡知识库（CoffeeKB）—— 就是那份《咖啡风味手册》
# 里面是一段一段的咖啡知识，第二阶段做"搜资料"就靠查这张表
# ============================================================
class CoffeeKB(Base):
    """《咖啡风味手册》知识库表 —— 第二阶段检索用"""

    __tablename__ = "coffee_kb"

    id = Column(_PK, primary_key=True, autoincrement=True)               # 这条知识的编号
    coffee_name = Column(String(128), nullable=False)                    # 咖啡叫啥名
    content = Column(Text, nullable=False)                               # 这一段写了啥（风味描述之类的长文本）。用 Text 是因为内容可能很长，String 有字数上限
    price = Column(DECIMAL(10, 2), nullable=False, default=Decimal("0.00"))  # 价格
    tags = Column(String(255), nullable=True)                            # 标签，比如 "果香,清甜,无奶"（后面靠模糊匹配搜它）
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)  # 啥时候加进来的


# ============================================================
# 【A2A 扩展】第四张表：游戏会话表（GameSession）
# 记录每个用户在像素咖啡馆里的游戏状态：角色位置、场景、事件日志
# ============================================================
class GameSession(Base):
    """游戏会话表：记录像素咖啡馆的角色与场景状态"""

    __tablename__ = "game_session"

    session_id = Column(String(64), primary_key=True)                    # 会话编号（UUID）
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False)  # 属于哪个用户
    actors = Column(JSON, nullable=False, default=list)                  # 角色列表（位置、状态、动画）
    scene = Column(JSON, nullable=True)                                  # 场景数据（家具、地板）
    last_event = Column(String(64), nullable=True)                       # 最近一次事件类型
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (Index("idx_game_user", "user_id"),)


# ============================================================
# 【A2A 扩展】第五张表：积分流水表（PointsTransaction）
# 记录每一笔积分变动（消费/充值/退款），用于积分审计与对账
# ============================================================
class PointsTransaction(Base):
    """积分流水表：记录积分的增减明细"""

    __tablename__ = "points_transaction"

    tx_id = Column(_PK, primary_key=True, autoincrement=True)            # 流水编号
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False)  # 属于哪个用户
    delta = Column(Integer, nullable=False)                              # 积分变动量（正=充值，负=消费）
    balance_after = Column(Integer, nullable=False)                      # 变动后的余额（便于审计）
    reason = Column(String(255), nullable=False)                         # 变动原因，如 "点单:美式咖啡"
    order_id = Column(BigInteger, nullable=True)                         # 关联订单（点单消费时填）
    source = Column(String(32), nullable=False, default="web")           # 来源：web / skill / evomap
    evomap_tx_id = Column(String(128), nullable=True)                    # EvoMap 交易ID（对接时填）
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_points_user", "user_id", "created_at"),)


# ============================================================
# 【A2A 扩展】第六张表：Skill 调用日志表（SkillLog）
# 记录其他 Agent（Claude Code / Codex 等）通过 Skill 点单的调用记录
# ============================================================
class SkillLog(Base):
    """Skill 调用日志表：记录 Agent 通过 Skill 发起的操作"""

    __tablename__ = "skill_log"

    log_id = Column(_PK, primary_key=True, autoincrement=True)           # 日志编号
    skill_id = Column(String(64), nullable=False)                        # 调用的 Skill 名，如 order_coffee
    user_id = Column(BigInteger, ForeignKey("user.user_id"), nullable=False)  # 发起用户
    agent_type = Column(String(32), nullable=False, default="unknown")   # Agent 类型：claude / codex / cursor
    params = Column(JSON, nullable=True)                                 # 调用参数
    result = Column(JSON, nullable=True)                                 # 返回结果
    points_cost = Column(Integer, nullable=False, default=0)            # 消耗积分
    success = Column(SmallInteger, nullable=False, default=1)            # 是否成功（1成功 / 0失败）
    error_msg = Column(Text, nullable=True)                              # 失败原因
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (Index("idx_skill_user", "user_id", "created_at"),)
