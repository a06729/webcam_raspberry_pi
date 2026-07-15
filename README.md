# Raspberry Pi MQTT 카메라 클라이언트

api_server 가 MQTT 로 `capture` 명령을 보내면 라즈베리파이가 사진을 찍어
JPEG 을 다시 MQTT 로 발행하는 기기 쪽 클라이언트.

## 동작 방식 (api_server/mqtt_client.py 와 동일한 토픽 규약)

| 방향 | 토픽 | 내용 |
|---|---|---|
| 서버 → 기기 | `esp32cam/{device_key}/cmd` | `capture` 명령 |
| 기기 → 서버 | `esp32cam/{device_key}/image` | JPEG 바이너리 |
| 기기 → 서버 | `esp32cam/{device_key}/status` | `online` / `offline` (retain, LWT 포함) |

서버의 `capture_and_wait()` 가 명령을 보내고 이미지 수신까지 기다리므로,
`device_key` 가 서버 DB(devices 테이블)에 등록돼 있어야 서버가 해당 토픽을 구독한다.

## 환경 변수 (.env)

`.env.example` 을 복사해서 `.env` 를 만들고 값을 채운다. (`.env` 는 `.gitignore` 에 포함되어 커밋되지 않는다)

```bash
# api_server 와 같은 MQTT 브로커 주소
MQTT_BROKER=your-mqtt-broker-host
MQTT_PORT=1883
# MQTT_USERNAME=
# MQTT_PASSWORD=

# 서버 DB(devices 테이블)에 등록된 device_key 와 일치해야 함
DEVICE_KEY=raspi-cam-01

# API 서버 주소 — 시작 시 이 서버에 기기를 자동 등록 (비우면 건너뜀)
API_BASE_URL=http://your-api-server-host:8000
DEVICE_NAME=Raspberry Pi Camera
# LATITUDE=37.5665
# LONGITUDE=126.9780

# picam(라즈베리파이 카메라 모듈) | usb(웹캠)
CAMERA_BACKEND=usb
CAMERA_WIDTH=1280
CAMERA_HEIGHT=720

# online 하트비트 간격(초), 0이면 끔
STATUS_INTERVAL=60
```

`MQTT_BROKER`, `API_BASE_URL` 은 실제 브로커/서버 주소로 바꿔야 하며,
이 값들은 환경마다 다르고 민감할 수 있으므로 README 등 저장소에 커밋되는 문서에는
실제 IP 대신 위처럼 플레이스홀더로만 표기한다.

## 설치 (라즈베리파이에서)

```bash
sudo apt install -y python3-picamera2        # 카메라 모듈 사용 시
pip install -r requirements.txt
cp .env.example .env                          # 브로커 주소·device_key 수정
```

USB 웹캠을 쓰려면 `.env` 에서 `CAMERA_BACKEND=usb` 로 바꾸고
`pip install opencv-python-headless` 를 추가로 설치한다.

## 실행

```bash
python mqtt_camera_client.py
```

부팅 시 자동 실행하려면 systemd 서비스 예시:

```ini
# /etc/systemd/system/mqtt-camera.service
[Unit]
Description=MQTT Camera Client
After=network-online.target

[Service]
WorkingDirectory=/home/pi/esp32_app/raspberry_pi
ExecStart=/usr/bin/python3 mqtt_camera_client.py
Restart=always
User=pi

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now mqtt-camera
```

## 테스트

서버 없이 브로커만으로 확인:

```bash
mosquitto_pub -h <broker> -t "esp32cam/raspi-cam-01/cmd" -m capture
mosquitto_sub -h <broker> -t "esp32cam/raspi-cam-01/image" -C 1 > test.jpg
```
