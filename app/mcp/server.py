# -*- coding: utf-8 -*-
"""
MCP Server：让 Claude Code / Cursor / Codex 等 Agent 通过 MCP 协议调用 Skill 点单

两种运行模式：
  1. stdio 模式（标准）：Agent 通过子进程 + stdin/stdout 调用
  2. HTTP 模式：Agent 通过 HTTP 调用（更简单，适配更多 Agent）

使用方式（在 Claude Code 的 MCP 配置中添加）：
  {
    "mcpServers": {
      "a2a-super-order": {
        "command": "python",
        "args": ["-m", "app.mcp.server"],
        "env": {
          "DB_MODE": "sqlite",
          "MEMORY_MODE": "fake"
        }
      }
    }
  }
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

logger = logging.getLogger("a2a.mcp")

# ============================================================
# MCP Tool 定义（JSON-RPC 格式）
# ============================================================
TOOLS = [
    {
        "name": "order_coffee",
        "description": "在 A2A 超级点单系统点一杯咖啡。会消耗积分并触发像素游戏可视化动画。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "用户ID（数字），不知道就传 1",
                },
                "coffee_name": {
                    "type": "string",
                    "description": "想喝的咖啡名，如 美式咖啡、拿铁、卡布奇诺",
                },
                "quantity": {
                    "type": "integer",
                    "description": "数量，默认 1",
                    "default": 1,
                },
                "special_requests": {
                    "type": "string",
                    "description": "特殊要求，如 不加糖、多加冰",
                    "default": "",
                },
            },
            "required": ["user_id", "coffee_name"],
        },
    },
    {
        "name": "get_menu",
        "description": "获取 A2A 超级点单系统的咖啡菜单，返回所有可点的咖啡及价格。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "check_points",
        "description": "查询用户在 EvoMap 的积分余额。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {
                    "type": "integer",
                    "description": "用户ID（数字）",
                },
            },
            "required": ["user_id"],
        },
    },
    {
        "name": "query_order",
        "description": "根据订单ID查询订单状态。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "order_id": {
                    "type": "integer",
                    "description": "订单ID",
                },
            },
            "required": ["order_id"],
        },
    },
    {
        "name": "recharge_points",
        "description": "给用户充值积分（测试/演示用）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user_id": {"type": "integer", "description": "用户ID"},
                "amount": {"type": "integer", "description": "充值积分数"},
            },
            "required": ["user_id", "amount"],
        },
    },
]


def handle_tool_call(name: str, arguments: dict) -> str:
    """处理工具调用，返回结果文本"""
    from app.db.database import SessionLocal
    from app.skill.service import (
        order_coffee_via_skill,
        get_menu_via_skill,
        check_points_via_skill,
        query_order_via_skill,
    )
    from app.evomap.client import evomap_client

    db = SessionLocal()
    try:
        if name == "order_coffee":
            result = order_coffee_via_skill(
                db,
                user_id=arguments["user_id"],
                coffee_name=arguments["coffee_name"],
                agent_type="mcp",
                quantity=arguments.get("quantity", 1),
                special_requests=arguments.get("special_requests", ""),
            )
        elif name == "get_menu":
            result = get_menu_via_skill(db)
        elif name == "check_points":
            result = check_points_via_skill(db, arguments["user_id"])
        elif name == "query_order":
            result = query_order_via_skill(db, arguments["order_id"])
        elif name == "recharge_points":
            evomap_client.recharge_points(
                db, arguments["user_id"], arguments["amount"], reason="MCP充值"
            )
            result = check_points_via_skill(db, arguments["user_id"])
        else:
            result = {"error": f"未知工具: {name}"}

        return json.dumps(result, ensure_ascii=False, default=str)
    finally:
        db.close()


def run_stdio_server() -> None:
    """
    stdio 模式 MCP Server（标准 MCP 协议）。

    通信格式：每行一个 JSON-RPC 2.0 消息
      → {"jsonrpc":"2.0","id":1,"method":"initialize",...}
      ← {"jsonrpc":"2.0","id":1,"result":{...}}

    支持的 method：
      - initialize
      - tools/list
      - tools/call
    """
    logger.info("[MCP] stdio server 启动")

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue

        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        msg_id = msg.get("id")
        method = msg.get("method", "")

        response: dict[str, Any] = {"jsonrpc": "2.0", "id": msg_id}

        if method == "initialize":
            response["result"] = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {
                    "name": "a2a-super-order",
                    "version": "1.0.0",
                },
                "capabilities": {"tools": {}},
            }

        elif method == "tools/list":
            response["result"] = {"tools": TOOLS}

        elif method == "tools/call":
            params = msg.get("params", {})
            tool_name = params.get("name")
            arguments = params.get("arguments", {})
            try:
                result_text = handle_tool_call(tool_name, arguments)
                response["result"] = {
                    "content": [{"type": "text", "text": result_text}]
                }
            except Exception as e:
                response["error"] = {"code": -32603, "message": str(e)}

        elif method == "notifications/initialized":
            # 通知，无需响应
            continue

        else:
            response["error"] = {"code": -32601, "message": f"Method not found: {method}"}

        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


def run_http_server(host: str = "0.0.0.0", port: int = 9000) -> None:
    """
    HTTP 模式 MCP Server（简化版，方便调试和 Codex 等 Agent 接入）。

    端点：
      GET  /mcp/tools     → 返回工具列表
      POST /mcp/call      → 调用工具
    """
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    import uvicorn

    app = FastAPI(title="A2A Super Order MCP Server")
    app.add_middleware(
        CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"]
    )

    @app.get("/mcp/tools")
    def list_tools():
        return {"tools": TOOLS}

    @app.post("/mcp/call")
    def call_tool(body: dict):
        name = body.get("name")
        arguments = body.get("arguments", {})
        try:
            result = handle_tool_call(name, arguments)
            return {"success": True, "result": json.loads(result)}
        except Exception as e:
            return {"success": False, "error": str(e)}

    @app.get("/")
    def health():
        return {"service": "a2a-super-order-mcp", "status": "ok"}

    logger.info(f"[MCP] HTTP server 启动: http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")

    # 默认 stdio 模式；传 --http 启动 HTTP 模式
    if "--http" in sys.argv:
        port = 9000
        for i, arg in enumerate(sys.argv):
            if arg == "--port" and i + 1 < len(sys.argv):
                port = int(sys.argv[i + 1])
        run_http_server(port=port)
    else:
        run_stdio_server()
