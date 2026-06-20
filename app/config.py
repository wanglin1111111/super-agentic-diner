from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 运行模式：sqlite / mysql  —— sqlite 零依赖便于演示，mysql 走真实环境
    db_mode: str = "mysql"
    # 记忆模式：fake / redis  —— fake 用进程内 fakeredis，redis 走真实环境
    memory_mode: str = "redis"

    # MySQL（db_mode=mysql 时生效）
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_user: str = "coffee"
    mysql_password: str = "coffee123"
    mysql_database: str = "coffee_ai"

    # SQLite 文件路径（db_mode=sqlite 时生效）
    sqlite_path: str = "./coffee.db"

    # Redis（memory_mode=redis 时生效）
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # LLM（OpenAI 兼容）
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"

    # 对话记忆
    chat_history_rounds: int = 5
    chat_history_ttl: int = 1800

    # ===== A2A 超级点单系统扩展配置 =====
    # EvoMap 积分系统
    evomap_api_key: str = ""
    evomap_base_url: str = "https://api.evomap.ai"
    evomap_mock: bool = True  # 无 key 时用本地模拟积分

    # 游戏可视化
    game_enabled: bool = True
    game_grid_width: int = 24
    game_grid_height: int = 16
    game_tile_size: int = 32

    # MCP / Skill 服务
    mcp_enabled: bool = True
    skill_order_cost: int = 10  # 每次点单消耗的积分

    @property
    def database_url(self) -> str:
        if self.db_mode == "sqlite":
            return f"sqlite:///{self.sqlite_path}"
        return (
            f"mysql+pymysql://{self.mysql_user}:{self.mysql_password}"
            f"@{self.mysql_host}:{self.mysql_port}/{self.mysql_database}?charset=utf8mb4"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/{self.redis_db}"


settings = Settings()
