"""라즈베리파이 MQTT 카메라 클라이언트.

api_server 의 CameraMqtt(api_server/mqtt_client.py)와 짝을 이루는 기기 쪽 코드.
서버가 esp32cam/{device_key}/cmd 로 "capture" 를 보내면 사진을 찍어
esp32cam/{device_key}/image 로 JPEG 바이너리를 발행한다.

토픽 구조 (서버와 동일):
  기기 → 서버: esp32cam/{device_key}/image   (JPEG 바이너리)
  기기 → 서버: esp32cam/{device_key}/status  ("online"/"offline")
  서버 → 기기: esp32cam/{device_key}/cmd     (명령 문자열, 예: "capture")

실행:
  python mqtt_camera_client.py
"""
from __future__ import annotations

import logging
import signal
import threading

import paho.mqtt.client as mqtt

from camera import Camera, CameraError
from config import settings
from register import register_device

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
log = logging.getLogger("raspi-cam")

TOPIC_IMAGE = f"esp32cam/{settings.device_key}/image"
TOPIC_STATUS = f"esp32cam/{settings.device_key}/status"
TOPIC_CMD = f"esp32cam/{settings.device_key}/cmd"


class CameraClient:
    def __init__(self) -> None:
        self._camera = Camera()
        self._stop = threading.Event()

        self._client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"raspi-{settings.device_key}",
        )
        if settings.mqtt_username:
            self._client.username_pw_set(settings.mqtt_username, settings.mqtt_password)

        # 비정상 종료 시 브로커가 대신 offline 을 발행 (Last Will)
        self._client.will_set(TOPIC_STATUS, "offline", qos=1, retain=True)

        self._client.on_connect = self._on_connect
        self._client.on_disconnect = self._on_disconnect
        self._client.on_message = self._on_message
        self._client.enable_logger(logging.getLogger("paho"))

    # ------------------------------------------------------------------ #
    # 실행/종료
    # ------------------------------------------------------------------ #
    def run(self) -> None:
        log.info("MQTT 연결 시도 %s:%s (device_key=%s)",
                 settings.mqtt_broker, settings.mqtt_port, settings.device_key)
        self._client.connect_async(
            settings.mqtt_broker, settings.mqtt_port, settings.mqtt_keepalive
        )
        self._client.loop_start()

        try:
            self._stop.wait()
        finally:
            self.shutdown()

    def _heartbeat_loop(self) -> None:
        while not self._stop.wait(timeout=settings.status_interval):
            if self._client.is_connected():
                self._client.publish(TOPIC_STATUS, "online", qos=1, retain=True)

    def shutdown(self) -> None:
        log.info("종료 중…")
        try:
            # 정상 종료 알림 (LWT 는 비정상 종료에만 동작하므로 직접 발행)
            info = self._client.publish(TOPIC_STATUS, "offline", qos=1, retain=True)
            info.wait_for_publish(timeout=3)
        except Exception:
            pass
        self._client.loop_stop()
        self._client.disconnect()
        self._camera.close()

    def stop(self) -> None:
        self._stop.set()

    # ------------------------------------------------------------------ #
    # MQTT 콜백 (paho 네트워크 스레드에서 실행)
    # ------------------------------------------------------------------ #
    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            log.error("MQTT 연결 실패: %s", reason_code)
            return
        log.info("MQTT 연결됨 — %s 구독", TOPIC_CMD)
        client.subscribe(TOPIC_CMD, qos=1)
        client.publish(TOPIC_STATUS, "online", qos=1, retain=True)

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        log.warning("MQTT 연결 끊김: %s (자동 재연결 대기)", reason_code)

    def _on_message(self, client, userdata, msg: mqtt.MQTTMessage):
        command = msg.payload.decode(errors="replace").strip().lower()
        log.info("명령 수신: %r", command)

        if command == "capture":
            # 캡처가 수 초 걸릴 수 있으므로 네트워크 스레드를 막지 않도록 분리
            threading.Thread(target=self._capture_and_publish, daemon=True).start()
        else:
            log.warning("알 수 없는 명령 무시: %r", command)

    # ------------------------------------------------------------------ #
    # 캡처 → 발행
    # ------------------------------------------------------------------ #
    def _capture_and_publish(self) -> None:
        try:
            jpeg = self._camera.capture_jpeg()
        except CameraError as exc:
            log.error("캡처 실패: %s", exc)
            self._client.publish(TOPIC_STATUS, f"error: {exc}", qos=1)
            return
        except Exception as exc:
            log.exception("캡처 중 예기치 못한 오류")
            self._client.publish(TOPIC_STATUS, f"error: {exc}", qos=1)
            return

        info = self._client.publish(TOPIC_IMAGE, jpeg, qos=1)
        info.wait_for_publish(timeout=10)
        log.info("이미지 발행 완료: %d bytes → %s", len(jpeg), TOPIC_IMAGE)


def main() -> None:
    # API 서버에 기기 등록 (upsert — 매 시작마다 호출해도 안전).
    # 등록돼야 서버가 이 기기의 image/status 토픽을 구독한다.
    register_device()

    client = CameraClient()

    def _handle_signal(signum, frame):
        client.stop()

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    # 주기적 online 하트비트
    if settings.status_interval > 0:
        threading.Thread(target=client._heartbeat_loop, daemon=True).start()

    client.run()


if __name__ == "__main__":
    main()
