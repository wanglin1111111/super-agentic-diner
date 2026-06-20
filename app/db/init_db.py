# -*- coding: utf-8 -*-
"""
数据库初始化脚本
创建所有表（含 A2A 扩展表）并灌入测试数据
"""
from app.db.database import Base, engine
from app.db import models  # noqa: F401  # 确保所有模型被导入
from app.db.seed import seed


def init_db() -> None:
    """创建所有表 + 灌入种子数据"""
    print("[init] 开始创建数据库表...")
    Base.metadata.create_all(bind=engine)
    print("[init] 表创建完成")
    print("[init] 开始灌入种子数据...")
    seed()
    print("[init] 种子数据灌入完成")


if __name__ == "__main__":
    init_db()
