"""API 서버에 웹캠 자동 등록.

api_server 의 POST /webcams 는 device_key 기준 upsert 이므로
매 시작마다 호출해도 안전하다. device_type='webcam' 으로 저장되어
앱용 ESP32 목록(GET /devices)에는 나타나지 않는다.
등록되면 서버가 해당 기기의 MQTT 토픽 구독을 자동으로 추가한다.
"""
from __future__ import annotations

import logging
import time

import requests

from config import settings

log = logging.getLogger("register")


def register_device(retries: int = 3, retry_delay: float = 3.0) -> bool:
    """서버에 자기 자신을 등록한다. 성공하면 True.

    api_base_url 이 설정돼 있지 않으면 건너뛴다(True 반환).
    서버가 아직 안 떠 있을 수 있으므로 몇 번 재시도한다.
    """
    if not settings.api_base_url:
        log.info("API_BASE_URL 미설정 — 기기 자동 등록 건너뜀")
        return True

    url = settings.api_base_url.rstrip("/") + "/webcams"
    payload: dict = {"device_key": settings.device_key}
    if settings.device_name:
        payload["name"] = settings.device_name

    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json=payload, timeout=10)
            resp.raise_for_status()
            log.info("기기 등록 완료: %s → %s", settings.device_key, url)
            return True
        except requests.RequestException as exc:
            log.warning("기기 등록 실패 (%d/%d): %s", attempt, retries, exc)
            if attempt < retries:
                time.sleep(retry_delay)

    log.error("기기 등록에 끝내 실패 — 서버가 이 기기의 토픽을 구독하지 않으면 "
              "이미지가 저장되지 않습니다 (%s)", url)
    return False
