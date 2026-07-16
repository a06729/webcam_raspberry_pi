"""라즈베리파이 카메라 MQTT 클라이언트 설정.

환경변수 또는 같은 폴더의 .env 파일로 덮어쓸 수 있다.
api_server/config.py 와 동일하게 pydantic-settings 를 사용한다.
"""
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# 실행 위치(cwd)와 무관하게 이 파일이 있는 폴더의 .env 를 읽는다.
_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_prefix="", extra="ignore")

    # --- MQTT 브로커 (api_server 와 같은 브로커를 바라봐야 함) ---
    mqtt_broker: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_keepalive: int = 60

    # --- 기기 식별 ---
    # 서버 DB(devices 테이블)에 등록된 device_key 와 반드시 일치해야 한다.
    device_key: str = "raspi-cam-01"

    # --- API 서버 (웹캠 자동 등록용) ---
    # 시작 시 POST {api_base_url}/webcams 로 자기 자신을 등록한다.
    # 비워두면 등록을 건너뛴다.
    api_base_url: str | None = None      # 예) http://15.168.153.75:8000
    device_name: str | None = "Raspberry Pi Camera"

    # --- 카메라 ---
    # picam  : 라즈베리파이 카메라 모듈 (Picamera2)
    # usb    : USB 웹캠 (OpenCV)
    camera_backend: str = "picam"
    camera_width: int = 1280
    camera_height: int = 720
    jpeg_quality: int = 85          # usb(OpenCV) 백엔드에서만 사용
    usb_camera_index: int = 0       # usb 백엔드에서 /dev/video{N}

    # 상태(online) 주기 보고 간격(초). 0 이면 비활성.
    status_interval: int = 60

    # --- BLE 와이파이 프로비저닝 ---
    # 시작 시 와이파이가 안 붙어 있으면 BLE 로 SSID/비밀번호를 수신해 연결한다.
    ble_provision: bool = True
    ble_name: str = "raspi-cam-setup"   # 노트북에서 스캔할 BLE 광고 이름
    wifi_interface: str = "wlan0"

    # BLE GATT UUID — 노트북 쪽(wifi_manager/.env)과 반드시 동일해야 한다.
    # 환경변수 이름(SERVICE_UUID 등)도 wifi_manager 와 같아 값을 그대로 복사하면 된다.
    service_uuid: str = "8e0d0001-7d4f-4f2a-9a6b-3c1f2a5d9e10"
    creds_char_uuid: str = "8e0d0002-7d4f-4f2a-9a6b-3c1f2a5d9e10"   # write: 와이파이 정보 수신
    status_char_uuid: str = "8e0d0003-7d4f-4f2a-9a6b-3c1f2a5d9e10"  # read/notify: 연결 상태 회신


settings = Settings()
