"""pytest 配置：使用独立的测试数据库，避免污染开发数据。"""

import os
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置环境变量，使 DB_PATH 指向测试库
_tmp = tempfile.mkdtemp(prefix="training_test_")
os.environ.setdefault("TRAINING_ENC_KEY", "")  # 测试期不加密

# 通过 monkeypatch DB_PATH：在 config 模块导入前重写
os.environ["TRAINING_TEST_DB"] = str(Path(_tmp) / "test.db")

import pytest  # noqa: E402

import app.config as _cfg  # noqa: E402

_cfg.DB_PATH = Path(os.environ["TRAINING_TEST_DB"])
_cfg.DATA_DIR = Path(_tmp)


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个测试前重建表，提供真正的测试隔离。

    原实现所有 DB 测试共用同一测试库文件并依赖各自的幂等种子逻辑，
    一旦某个测试留下数据（如题目/用户）就会让后续测试的"若已存在则跳过"
    守卫误判、静默跳过种子，导致断言失败。重建表后每个测试都从空库起步。
    """
    from sqlalchemy import text

    import app.models  # noqa: F401  确保所有模型注册到 metadata
    from app.database import Base, engine

    # 关闭外键约束后再 drop/create，避免 use_alter 约束导致的 DROP 顺序冲突
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=ON"))
    yield
