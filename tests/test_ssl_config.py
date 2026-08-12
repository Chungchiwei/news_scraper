"""
test_ssl_config.py
Phase 2.1 §十六：SSL/TLS verify 必須可設定，不可 blanket 全域關閉。

不依賴任何真實網路連線 —— 全部透過 mock `_download_rss` 攔截
verify 參數，只驗證「設定值有沒有正確傳遞」，不實際發 HTTP request。
"""

import importlib
from unittest.mock import patch

import maritime_news as mn


def _make_scraper():
    return mn.NewsRssScraper(keywords=[], sources=[], cnyes_sources=[], hours_back=6)


def test_ssl_default_verify_enabled(monkeypatch):
    """
    未設定 SSL_VERIFY 環境變數時，模組層級預設值必須是 True
    （不能預設關閉憑證驗證）。
    """
    assert mn.SSL_VERIFY is True

    # 模擬未設定環境變數時 reload 模組也應該得到 True
    monkeypatch.delenv("SSL_VERIFY", raising=False)
    # 只重新計算同樣的運算式，不整個 reload 模組（避免副作用），
    # 直接驗證 os.getenv 預設邏輯本身。
    import os
    default_val = os.getenv("SSL_VERIFY", "true").strip().lower() not in (
        "false", "0", "no", "",
    )
    assert default_val is True


def test_ssl_per_source_override(monkeypatch):
    """
    來源設定明確帶 "verify_ssl": false 時，必須覆寫全域 SSL_VERIFY，
    且該 verify 值必須一路傳進 _download_rss()。
    全域 SSL_VERIFY 保持 True 不受影響（其他來源仍然驗證憑證）。
    """
    scraper = _make_scraper()
    captured = {}

    def fake_download_rss(url, need_clean=False, is_cn=False, verify=True):
        captured["verify"] = verify
        return None  # 沒有資料，fetch_from_source 會直接回傳 []

    with patch.object(scraper, "_download_rss", side_effect=fake_download_rss):
        source = {
            "name": "Test Insecure Source",
            "icon": "🧪",
            "url": "https://example-insecure.test/rss",
            "verify_ssl": False,
        }
        scraper.fetch_from_source(source)

    assert captured["verify"] is False
    # 全域開關不應該被單一來源覆寫影響
    assert mn.SSL_VERIFY is True


def test_ssl_source_without_override_uses_global_default():
    """
    沒有設定 verify_ssl 的來源，必須直接沿用全域 SSL_VERIFY（預設 True），
    不能因為別的來源關閉了驗證就連帶被關閉。
    """
    scraper = _make_scraper()
    captured = {}

    def fake_download_rss(url, need_clean=False, is_cn=False, verify=True):
        captured["verify"] = verify
        return None

    with patch.object(scraper, "_download_rss", side_effect=fake_download_rss):
        source = {
            "name": "Test Normal Source",
            "icon": "🧪",
            "url": "https://example-normal.test/rss",
        }
        scraper.fetch_from_source(source)

    assert captured["verify"] == mn.SSL_VERIFY
    assert captured["verify"] is True
