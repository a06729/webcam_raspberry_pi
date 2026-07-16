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

# BLE 와이파이 프로비저닝 (자세한 설명은 아래 "BLE 와이파이 프로비저닝" 절 참고)
BLE_PROVISION=true
BLE_NAME=raspi-cam-setup
WIFI_INTERFACE=wlan0

# BLE GATT UUID — 노트북 쪽(wifi_manager/.env)의 같은 이름 항목과 반드시 동일해야 함
SERVICE_UUID=<SERVICE_UUID 값>
CREDS_CHAR_UUID=<CREDS_CHAR_UUID 값>
STATUS_CHAR_UUID=<STATUS_CHAR_UUID 값>
```

`MQTT_BROKER`, `API_BASE_URL` 은 실제 브로커/서버 주소로 바꿔야 하며,
이 값들은 환경마다 다르고 민감할 수 있으므로 README 등 저장소에 커밋되는 문서에는
실제 IP 대신 위처럼 플레이스홀더로만 표기한다.

## 설치 (라즈베리파이에서)

공통 준비:

```bash
sudo apt install -y python3-picamera2        # 카메라 모듈 사용 시
cp .env.example .env                          # 브로커 주소·device_key 수정
```

파이썬 패키지는 아래 **두 가지 방법 중 하나**로 설치한다.
어느 쪽을 쓰느냐에 따라 뒤의 systemd `ExecStart` 경로가 달라진다.

### 방법 A — 시스템 파이썬에 설치 (venv 없이)

```bash
pip install --break-system-packages bless paho-mqtt pydantic-settings requests
```

- 실행: `python3 mqtt_camera_client.py`
- Raspberry Pi OS Bookworm 부터는 시스템 파이썬에 pip 설치가 기본 차단되어
  `--break-system-packages` 플래그가 필요하다.
- apt 로 설치한 `picamera2` 를 별도 설정 없이 바로 쓸 수 있다.

### 방법 B — .venv 가상환경에 설치 (권장)

```bash
# picamera2(apt 패키지)를 venv 안에서도 쓸 수 있도록 --system-site-packages 필수
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
pip install bless paho-mqtt pydantic-settings requests
```

- 실행: `.venv/bin/python mqtt_camera_client.py` (또는 activate 후 `python mqtt_camera_client.py`)
- 시스템 파이썬을 오염시키지 않아 다른 프로젝트와 충돌이 없다.
- ⚠️ `--system-site-packages` 없이 만든 venv 에서는 `picamera2` import 가 실패한다.
  이미 만든 venv 가 있다면 다음으로 확인하고, 실패하면 지우고 다시 만든다:

  ```bash
  .venv/bin/python -c "import picamera2; print('OK')"
  ```

USB 웹캠을 쓰는 경우(두 방법 공통): `.env` 에서 `CAMERA_BACKEND=usb` 로 바꾸고
`pip install opencv-python-headless` 를 추가로 설치한다.
(usb 백엔드만 쓰면 picamera2 는 필요 없다)

## 실행

```bash
python mqtt_camera_client.py
```

### 부팅 시 자동 실행 (systemd)

서비스 파일은 하나이고, **설치 방법(A/B)에 따라 `ExecStart` 한 줄만 달라진다.**

```ini
# /etc/systemd/system/mqtt-camera.service
[Unit]
Description=MQTT Camera Client (BLE WiFi provisioning + MQTT)
# network-online.target 을 기다리면 안 된다 — 와이파이가 없을 때
# BLE 로 SSID/비밀번호를 받아 연결하는 것이 이 프로그램의 첫 단계다.
After=bluetooth.service dbus.service
Wants=bluetooth.service

[Service]
# 프로젝트를 복제해 둔 실제 경로로 수정
WorkingDirectory=/home/pi/esp32_app/raspberry_pi_webcam

# ── 방법 A(시스템 파이썬)로 설치한 경우 ──
ExecStart=/usr/bin/python3 mqtt_camera_client.py

# ── 방법 B(.venv)로 설치한 경우 — 위 줄 대신 이 줄 사용 ──
# venv 의 python 절대경로를 지정하면 activate 없이 그 가상환경으로 실행된다.
#ExecStart=/home/pi/esp32_app/raspberry_pi_webcam/.venv/bin/python mqtt_camera_client.py

# BLE GATT 서버(bless)가 시스템 D-Bus(BlueZ)에 등록하고 nmcli 로
# 와이파이 프로필을 만들려면 root 권한이 필요하다. (A/B 공통)
User=root
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

> 방법 B 를 쓴다면 방법 A 의 `ExecStart` 줄을 지우거나 `#` 로 주석 처리하고,
> 방법 B 줄의 `#` 를 제거하면 된다. `ExecStart` 는 하나만 있어야 한다.

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now mqtt-camera
```

상태/로그 확인:

```bash
systemctl status mqtt-camera     # active (running) 확인
journalctl -u mqtt-camera -f     # 실시간 로그 (BLE 대기 → 와이파이 연결 → MQTT)
```

## BLE 와이파이 프로비저닝

와이파이가 안 붙어 있으면 시작 시 BLE GATT 서버(`wifi_provision.py`)를 띄우고,
노트북에서 보내주는 SSID/비밀번호로 와이파이를 연결한 뒤 MQTT 로 진행한다.
이미 와이파이가 연결돼 있으면 이 단계는 건너뛴다.

```
┌──────────────────┐   BLE (SSID/비밀번호 JSON)   ┌──────────────────┐   nmcli    ┌────────┐
│ 노트북            │ ───────────────────────────▶ │ 라즈베리파이      │ ─────────▶ │ 와이파이 │
│ wifi_manager GUI │ ◀─────────────────────────── │ wifi_provision.py│            └────────┘
└──────────────────┘   상태 회신 (connecting /     └──────────────────┘
                       connected / failed)
```

파이에서 `mqtt_camera_client.py` 를 띄워두고, 노트북에서 둘 중 하나로 전송한다:

- **GUI**: `wifi_manager/wifi_manager_gui.py` 실행 → 스캔 → 기기 선택 → SSID/비밀번호 입력 → 전송
  (자세한 사용법은 `wifi_manager/README.md` 참고)
- **CLI**:

  ```bash
  pip install bleak
  python laptop_provision.py --ssid "MyWifi" --password "secret123"
  ```

연결에 실패하면(`failed` 회신) BLE 서버는 계속 대기하므로 노트북에서 SSID/비밀번호를
고쳐 바로 재전송하면 된다. 성공하면 `connected` 를 회신한 뒤 BLE 서버를 끄고
MQTT 클라이언트로 넘어간다.

### BLE 설정 (.env)

BLE 관련 값은 `.env` 로 바꿀 수 있다:

| 항목 | 기본값 | 설명 |
|---|---|---|
| `BLE_PROVISION` | `true` | 시작 시 BLE 프로비저닝 사용 여부 |
| `BLE_NAME` | `raspi-cam-setup` | BLE 광고 이름 (노트북 쪽 `RASPI_NAME` 과 동일해야 함) |
| `WIFI_INTERFACE` | `wlan0` | nmcli 가 사용할 무선 인터페이스 |
| `SERVICE_UUID` | `8e0d0001-…-3c1f2a5d9e10` | BLE GATT 서비스 UUID |
| `CREDS_CHAR_UUID` | `8e0d0002-…-3c1f2a5d9e10` | 와이파이 정보(쓰기) 캐릭터리스틱 UUID |
| `STATUS_CHAR_UUID` | `8e0d0003-…-3c1f2a5d9e10` | 연결 상태 회신(notify) 캐릭터리스틱 UUID |

> ⚠️ UUID 3개는 노트북 쪽 `wifi_manager/.env` 의 같은 이름 항목과 **반드시 동일**해야
> 한다. 환경변수 이름이 양쪽에서 같으므로 값을 그대로 복사하면 된다.

## 문제 해결: BLE 광고 등록 실패 (dbus 에러)

### 증상

`wifi_provision.py` 의 BLE 서버가 시작하자마자 죽는다:

```
dbus_next.errors.DBusError: Failed to register advertisement
```

`bluetoothctl advertise on` 도 똑같이 실패하고, `journalctl -u bluetooth` 에는:

```
Failed to add advertisement: Invalid Parameters (0x0d)
```

### 원인

파이썬 코드나 bless 의 문제가 아니라 **BlueZ 5.82 + 커널 6.18 조합의 시스템 버그**다.

BlueZ 5.82 의 `src/advertising.c` `add_adv_params_callback()` 은
`MGMT_OP_ADD_EXT_ADV_DATA` 명령의 길이를 `sizeof(struct mgmt_cp_add_advertising)`
(헤더 11바이트) 로 계산하는데, 실제로 보내는 구조체는 헤더가 3바이트인
`struct mgmt_cp_add_ext_adv_data` 다. 즉 **항상 8바이트를 초과해서** 커널에 넘긴다.

예전 커널은 이걸 눈감아 줬지만, 리눅스 6.18 이 이 명령의 길이를 정확히 검증하기
시작하면서(8바이트 slab OOB read 보안 수정) `Invalid Parameters (0x0d)` 로 거부한다.
결과적으로 **이 조합에서는 모든 LE 주변장치 광고가 실패**한다.

### 해결

upstream 과 동일하게 `sizeof(*cp)` 로 고쳐서 bluez 를 다시 빌드했다.

```bash
# deb-src 저장소 추가 (/etc/apt/sources.list.d/raspi-src.sources)
#   Types: deb-src
#   URIs: http://archive.raspberrypi.com/debian/
#   Suites: trixie
#   Components: main
#   Signed-By: /usr/share/keyrings/raspberrypi-archive-keyring.pgp

sudo apt update
sudo apt build-dep -y bluez
apt-get source bluez
cd bluez-5.82
```

`src/advertising.c` 의 `add_adv_params_callback()` 안(1443행 부근):

```diff
-	param_len = sizeof(struct mgmt_cp_add_advertising) + adv_data_len +
-							scan_rsp_len;
+	param_len = sizeof(*cp) + adv_data_len + scan_rsp_len;
```

> 같은 파일 985행(`refresh_legacy_adv`)에도 똑같은 표현이 있지만 그쪽은 실제로
> `struct mgmt_cp_add_advertising` 을 쓰는 구형 경로라 **정상이다. 건드리면 안 된다.**

```bash
DEB_BUILD_OPTIONS="nocheck parallel=4" dpkg-buildpackage -b -uc -us
sudo dpkg -i ../bluez_5.82-1.1+rpt1+extadvfix1_arm64.deb
sudo apt-mark hold bluez        # apt 가 깨진 배포판 버전으로 되돌리지 않도록 고정
sudo systemctl restart bluetooth
```

### 확인

```bash
dpkg -l bluez | tail -1          # 5.82-1.1+rpt1+extadvfix1, 상태 'hi' (hold)
bluetoothctl advertise on        # 에러 없이 등록되면 정상
```

### 재발 시

- `Failed to register advertisement` 가 다시 나오면 `journalctl -u bluetooth | grep "Failed to add advertisement"` 로 사유부터 확인한다.
  `Invalid Parameters (0x0d)` 면 hold 가 풀려 배포판 버전으로 되돌아간 것이다.
- `No Resources (0x07)` 은 **다른 문제**다. 광고 슬롯이 남아 있는 것이라
  `sudo systemctl restart bluetooth` 로 정리된다.
- 배포판이 고쳐진 bluez 를 내놓으면 `sudo apt-mark unhold bluez` 로 풀고 로컬 빌드를 버린다.

## 테스트

서버 없이 브로커만으로 확인:

```bash
mosquitto_pub -h <broker> -t "esp32cam/raspi-cam-01/cmd" -m capture
mosquitto_sub -h <broker> -t "esp32cam/raspi-cam-01/image" -C 1 > test.jpg
```
