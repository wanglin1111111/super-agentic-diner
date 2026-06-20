# -*- coding: utf-8 -*-
"""
A2A (Agent-to-Agent) Protocol 适配器模块

实现 Google A2A Protocol，让不同 AI Agent 可以发现、交互本咖啡店系统。

端点：
  - /.well-known/agent.json  — Agent Card（元数据，能力声明）
  - /api/a2a/tasks/{task_id} — Task 管理（创建、查询）
  - /api/a2a/tasks/{task_id}/messages — Message 发送

数据结构：
  - AgentCard   — Agent 元数据描述
  - Task        — 异步任务
  - Message     — Agent 间交互消息
  - Artifact    — 任务产出物
  - Part        — 消息内容片段（文本/文件）
"""
