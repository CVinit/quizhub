"""图形验证码单元测试。"""

from app.core.captcha import store


def test_captcha_generate_returns_id_and_image():
    cid, img = store.generate()
    assert isinstance(cid, str) and len(cid) > 0
    assert img.startswith("data:image/svg+xml;base64,")


def test_captcha_verify_correct_consumes():
    """正确验证码必须通过，且单次消费。

    原用例只断言「错码返回 False」：一个恒返回 False 的实现也能全绿（The Liar）。
    这里直接读内部答案做正向断言，并保留「消费后不可重放」的断言。
    """
    cid, _ = store.generate()
    answer = store._store[cid].answer  # 单测直接读内部答案，用于正向断言
    assert store.verify(cid, answer) is True
    assert store.verify(cid, answer) is False  # 已消费，防重放


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
