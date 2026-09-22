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
# 上传目录同样必须重定向：FILES_DIR 由 config 在导入期用 DATA_DIR 算出，
# 只改 DATA_DIR 不会改变它，漏掉会让"忘记 monkeypatch 的上传测试"写进真实数据目录。
_cfg.FILES_DIR = Path(_tmp) / "files"


@pytest.fixture(autouse=True)
def _fresh_db():
    """每个测试前重建表，提供真正的测试隔离。

    原实现所有 DB 测试共用同一测试库文件并依赖各自的幂等种子逻辑，
    一旦某个测试留下数据（如题目/用户）就会让后续测试的"若已存在则跳过"
    守卫误判、静默跳过种子，导致断言失败。重建表后每个测试都从空库起步。

    注意：`PRAGMA foreign_keys` 是**连接级**状态，必须在**同一条连接**上完成
    OFF → drop/create → ON。原实现把 OFF 与 ON 分别用 `engine.connect()` 取连接，
    一旦连接池里有不止一条连接（并发测试会把池撑大），两者可能落在不同连接上：
    一条被留在 foreign_keys=OFF，后续测试拿到它就会跳过 ON DELETE CASCADE，
    表现为「删除用户没有级联清理」这类与被测逻辑无关的随机失败。
    """
    from sqlalchemy import text

    import app.models  # noqa: F401  确保所有模型注册到 metadata
    from app.database import Base, engine

    # 关闭外键约束后再 drop/create，避免 use_alter 约束导致的 DROP 顺序冲突
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys=OFF"))
        Base.metadata.drop_all(bind=conn)
        Base.metadata.create_all(bind=conn)
        conn.execute(text("PRAGMA foreign_keys=ON"))
        conn.commit()
    _reset_process_state()
    yield


def _reset_process_state() -> None:
    """清空进程内单例（限流计数、验证码、上传预览缓存）。

    这些都是模块级单例，跨用例累积：`drop_all` 会把 AUTOINCREMENT 重置，
    于是每个用例的 user id 都从 1 开始，限流键（如 `upload-preview:user:1`）
    在不同用例间相互冲突，可能让后来者莫名 429。原先只靠"用例数没到阈值"侥幸通过。
    """
    from app.core.captcha import store as captcha_store
    from app.core.rate_limit import limiter
    from app.services import import_service
    from app.utils import user_excel

    limiter.reset()
    captcha_store.reset()
    import_service._preview_cache.clear()
    user_excel._preview_cache.clear()
