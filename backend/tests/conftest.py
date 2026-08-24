"""pytest 配置：使用独立的测试数据库，避免污染开发数据。"""
import os
import tempfile
from pathlib import Path

# 必须在导入 app 之前设置环境变量，使 DB_PATH 指向测试库
_tmp = tempfile.mkdtemp(prefix="training_test_")
os.environ.setdefault("TRAINING_ENC_KEY", "")  # 测试期不加密

# 通过 monkeypatch DB_PATH：在 config 模块导入前重写
os.environ["TRAINING_TEST_DB"] = str(Path(_tmp) / "test.db")

import app.config as _cfg  # noqa: E402
_cfg.DB_PATH = Path(os.environ["TRAINING_TEST_DB"])
_cfg.DATA_DIR = Path(_tmp)
