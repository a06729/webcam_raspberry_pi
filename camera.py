"""카메라 캡처 모듈.

두 가지 백엔드를 지원한다.
  - picam : 라즈베리파이 카메라 모듈 (Picamera2, Raspberry Pi OS 기본 탑재)
  - usb   : USB 웹캠 (OpenCV)

capture_jpeg() 는 JPEG 바이트를 반환한다. MQTT 콜백 스레드에서 호출되므로
카메라 접근은 락으로 직렬화한다.
"""
from __future__ import annotations

import io
import logging
import threading
import time

from config import settings

log = logging.getLogger("camera")


class CameraError(RuntimeError):
    pass


class _PicamBackend:
    def __init__(self) -> None:
        from picamera2 import Picamera2  # 라즈베리파이에서만 import 가능

        self._cam = Picamera2()
        # 포맷을 명시하지 않으면 일부 카메라에서 YUYV 로 협상되어
        # capture_file(format="jpeg") 시 KeyError: 'YUYV' 가 발생한다.
        cfg = self._cam.create_still_configuration(
            main={
                "size": (settings.camera_width, settings.camera_height),
                "format": "RGB888",
            }
        )
        self._cam.configure(cfg)

        # UVC(USB 웹캠)는 RGB888 요청이 무시되고 YUYV 로 협상된다.
        # 이 경우 capture_file(format="jpeg") 가 KeyError: 'YUYV' 로 죽으므로
        # 여기서 미리 감지해 명확한 안내와 함께 중단한다.
        negotiated = self._cam.camera_configuration()["main"]["format"]
        if negotiated not in ("RGB888", "BGR888", "XBGR8888", "XRGB8888"):
            self._cam.close()
            raise CameraError(
                f"카메라가 {negotiated} 포맷으로만 동작합니다 (USB 웹캠으로 보임). "
                ".env 에서 CAMERA_BACKEND=usb 로 바꾸고 "
                "opencv-python-headless 를 설치하세요."
            )

        self._cam.start()
        log.info("Picamera2 시작 (%dx%d, %s)",
                 settings.camera_width, settings.camera_height, negotiated)

    def capture_jpeg(self) -> bytes:
        buf = io.BytesIO()
        self._cam.capture_file(buf, format="jpeg")
        return buf.getvalue()

    def close(self) -> None:
        self._cam.stop()
        self._cam.close()


class _UsbBackend:
    def __init__(self) -> None:
        import cv2

        self._cv2 = cv2
        self._cap = cv2.VideoCapture(settings.usb_camera_index)
        if not self._cap.isOpened():
            raise CameraError(f"USB 카메라를 열 수 없습니다 (index={settings.usb_camera_index})")
        self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, settings.camera_width)
        self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, settings.camera_height)
        # 드라이버 버퍼를 최소화 — 버퍼에 쌓인 과거 프레임 때문에
        # 캡처가 한 프레임 이상 밀리는 것을 막는다 (V4L2 기본 버퍼는 4프레임).
        self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        log.info("USB 카메라 시작 (index=%d)", settings.usb_camera_index)

    def capture_jpeg(self) -> bytes:
        # 캡처 사이에 read 를 하지 않으므로 버퍼에는 직전 촬영 무렵의
        # 오래된 프레임이 남아 있다. grab() 은 버퍼가 비면 새 프레임을
        # 기다리므로, 짧은 시간 동안 반복해 버퍼를 완전히 비운다.
        deadline = time.monotonic() + 0.5
        flushed = 0
        while time.monotonic() < deadline and flushed < 8:
            self._cap.grab()
            flushed += 1
        ok, frame = self._cap.read()
        if not ok:
            raise CameraError("프레임 캡처 실패")
        ok, encoded = self._cv2.imencode(
            ".jpg", frame,
            [self._cv2.IMWRITE_JPEG_QUALITY, settings.jpeg_quality],
        )
        if not ok:
            raise CameraError("JPEG 인코딩 실패")
        return encoded.tobytes()

    def close(self) -> None:
        self._cap.release()


class Camera:
    """설정된 백엔드로 사진을 찍는 스레드 안전 래퍼."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        backend = settings.camera_backend.lower()
        if backend == "picam":
            self._backend = _PicamBackend()
        elif backend == "usb":
            self._backend = _UsbBackend()
        else:
            raise CameraError(f"알 수 없는 camera_backend: {backend!r} (picam|usb)")

    def capture_jpeg(self) -> bytes:
        with self._lock:
            return self._backend.capture_jpeg()

    def close(self) -> None:
        with self._lock:
            self._backend.close()
