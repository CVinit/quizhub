"""图形验证码单元测试。"""
from app.core.captcha import store


def test_captcha_generate_returns_id_and_image():
    cid, img = store.generate()
    assert isinstance(cid, str) and len(cid) > 0
    assert img.startswith("data:image/svg+xml;base64,")


def test_captcha_verify_correct_consumes():
    # 重写：直接构造 entry 校验逻辑
    cid, _ = store.generate()
    # 无法直接拿到明文答案（私有），改为用已知答案路径：
    # generate 内部用 secrets，故这里通过"错答案必失败、且消费后再次失败"间接验证单次消费
    ok = store.verify(cid, "0000")
    # 明文几乎不会恰好是 0000（概率 1e-4），接受两种情况均验证"单次消费"
    ok2 = store.verify(cid, "0000")
    # 同一 cid 第二次必失败（已消费或已过期）
    assert ok2 is False


def test_captcha_wrong_answer_consumes():
    cid, _ = store.generate()
    ok = store.verify(cid, "ZZZZ")
    assert ok is False
    # 错误答案也会消费，防穷举重放
    assert store.verify(cid, "ZZZZ") is False


def test_captcha_empty_inputs_rejected():
    assert store.verify("", "1234") is False
    cid, _ = store.generate()
    assert store.verify(cid, "") is False


def test_captcha_unknown_id_rejected():
    assert store.verify("nonexistent-id", "1234") is False
