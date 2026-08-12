#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teams_notifier.py
海事航運新聞監控系統 — Phase 7 §二十二、二十〜二十一 Teams Notifier

職責：
  定義 TeamsNotifier 抽象，把「怎麼把一則訊息送到 Teams webhook」跟
  「要不要送、送什麼內容」完全分開（webhook call 不寫進 maritime_news.py，
  §二十二）。

  ★ Failure Isolation（§二十〜二十一）：Teams 發送失敗只記
  status=FAILED + WARNING log，絕不能讓整個 intelligence pipeline
  crash，也絕不能讓 Email 因此發不出去——這點與 Phase 1 §可靠性修正
  的 SMTP retry 設計哲學一致，但『最終失敗』的後果不同：Email 最終
  失敗要 exit(1)（production policy），Teams 最終失敗只是 WARNING。
"""

from __future__ import annotations

import json
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TeamsSendResult:
    success: bool
    status_code: Optional[int] = None
    error: Optional[str] = None       # 人類可讀的簡短錯誤描述，不含 webhook URL 本身
    attempts: int = 0


class TeamsNotifier(ABC):
    @abstractmethod
    def send(self, webhook_url: str, message_text: str, timeout_seconds: int = 10) -> TeamsSendResult:
        """
        把訊息送到指定 webhook_url。message_text 是 teams_renderer.py 產生
        的純文字訊息；本層負責包裝成 Teams Incoming Webhook 接受的
        MessageCard JSON 並處理 HTTP 傳輸/重試，不做任何內容判斷。
        """
        raise NotImplementedError


def _build_message_card(message_text: str, theme_color: str = "0b1f3a") -> dict:
    """
    Office 365 Connector MessageCard 格式（Teams Incoming Webhook 廣泛
    支援的既有格式）。只用純文字 text 欄位承載內容——render 邏輯完全
    在 teams_renderer.py 決定，這裡不重新組字。
    """
    return {
        "@type": "MessageCard",
        "@context": "http://schema.org/extensions",
        "themeColor": theme_color,
        "summary": message_text.splitlines()[0] if message_text else "Maritime Intelligence",
        "text": message_text,
    }


class HttpTeamsNotifier(TeamsNotifier):
    """
    Production 預設實作：用 requests 呼叫 Incoming Webhook，有限次數
    重試（§二十）。★ 絕不把 webhook URL 或例外完整內容記進 log
    （webhook URL 本身即是機密——外洩等同於任何人都可以冒名發訊息）。
    """

    def __init__(self, max_retries: int = 3, retry_wait_seconds: int = 5):
        self.max_retries = max(1, max_retries)
        self.retry_wait_seconds = retry_wait_seconds

    def send(self, webhook_url: str, message_text: str, timeout_seconds: int = 10) -> TeamsSendResult:
        import requests  # 延遲載入：TEAMS_ENABLED=false 時完全不需要這個路徑

        if not webhook_url:
            return TeamsSendResult(success=False, error="webhook URL not configured", attempts=0)

        payload = _build_message_card(message_text)
        last_error: Optional[str] = None

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = requests.post(
                    webhook_url, data=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                    timeout=timeout_seconds,
                )
                if 200 <= resp.status_code < 300:
                    logger.info(f"✅ Teams 發送成功（第 {attempt} 次嘗試，HTTP {resp.status_code}）")
                    return TeamsSendResult(success=True, status_code=resp.status_code, attempts=attempt)
                last_error = f"HTTP {resp.status_code}"
                logger.warning(f"⚠️  Teams 發送失敗（第 {attempt}/{self.max_retries} 次）：{last_error}")
            except Exception as e:
                last_error = type(e).__name__
                logger.warning(f"⚠️  Teams 發送失敗（第 {attempt}/{self.max_retries} 次）：{last_error}")

            if attempt < self.max_retries:
                time.sleep(self.retry_wait_seconds)

        return TeamsSendResult(success=False, error=last_error, attempts=self.max_retries)


class FakeTeamsNotifier(TeamsNotifier):
    """
    測試/預覽專用：不呼叫任何真實 webhook，也不 sleep。

    ★ 跟 HttpTeamsNotifier 同一種契約：重試邏輯發生在單一次 send() 呼叫
    『內部』（呼叫端只呼叫一次 send()，不是自己迴圈呼叫多次）——
    fail_count 代表『內部前幾次模擬嘗試失敗，之後成功』，用來測試
    上層邏輯是否正確處理『最終成功』與 attempts 計數；always_fail
    代表『重試全部用盡仍失敗』，用來測試 Teams failure 不擋 Email
    （§二十一）。
    """

    def __init__(self, fail_count: int = 0, always_fail: bool = False, max_retries: int = 3):
        self.fail_count = fail_count
        self.always_fail = always_fail
        self.max_retries = max(1, max_retries)
        self.calls: list = []

    def send(self, webhook_url: str, message_text: str, timeout_seconds: int = 10) -> TeamsSendResult:
        self.calls.append({"webhook_url": webhook_url, "message_text": message_text})

        if not webhook_url:
            return TeamsSendResult(success=False, error="webhook URL not configured", attempts=0)

        for attempt in range(1, self.max_retries + 1):
            if self.always_fail:
                continue
            if attempt > self.fail_count:
                return TeamsSendResult(success=True, status_code=200, attempts=attempt)

        return TeamsSendResult(success=False, error="simulated failure (retries exhausted)",
                                attempts=self.max_retries)
