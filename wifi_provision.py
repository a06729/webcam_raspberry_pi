"""BLE 와이파이 프로비저닝 (라즈베리파이 쪽).

MQTT 연결 전에 와이파이가 안 붙어 있으면 BLE GATT 서버를 띄우고,
노트북(laptop_provision.py)이 보내주는 SSID/비밀번호로 와이파이를 연결한다.
연결 결과는 상태 캐릭터리스틱 notify 로 노트북에 회신한다.

프로토콜:
  노트북 → 파이 : CREDS 캐릭터리스틱에 JSON + '\n' 쓰기
                  {"ssid": "...", "password": "..."}
                  (BLE MTU 때문에 여러 조각으로 나뉘어 올 수 있어 '\n' 까지 누적)
  파이 → 노트북 : STATUS 캐릭터리스틱 notify (JSON)
                  {"status": "connecting" | "connected" | "failed", "detail": "..."}

와이파이 연결은 NetworkManager(nmcli)를 사용한다 (Raspberry Pi OS Bookworm 기본).

단독 실행 테스트:
  python wifi_provision.py
"""
from __future__ import annotations

import asyncio
import json
import logging
import subprocess
import time

from bless import (  # type: ignore[attr-defined]
    BlessServer,
    BlessGATTCharacteristic,
    GATTAttributePermissions,
    GATTCharacteristicProperties,
)

from config import settings

log = logging.getLogger("wifi-provision")

# 노트북 쪽(wifi_manager/wifi_manager_gui.py, laptop_provision.py)과 반드시
# 동일해야 한다. .env 파일(SERVICE_UUID 등, config.py 참고)로 덮어쓸 수 있다.
SERVICE_UUID = settings.service_uuid.lower()
CREDS_CHAR_UUID = settings.creds_char_uuid.lower()   # write: 와이파이 정보 수신
STATUS_CHAR_UUID = settings.status_char_uuid.lower()  # read/notify: 연결 상태 회신


# ------------------------------------------------------------------ #
# 와이파이 (nmcli)
# ------------------------------------------------------------------ #
def wifi_is_connected() -> bool:
    """wlan 인터페이스가 이미 연결돼 있는지 확인."""
    try:
        result = subprocess.run(
            ["nmcli", "-t", "-f", "DEVICE,TYPE,STATE", "device"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        log.warning("nmcli 상태 확인 실패: %s", exc)
        return False

    for line in result.stdout.splitlines():
        parts = line.split(":")
        if len(parts) >= 3 and parts[1] == "wifi" and parts[2] == "connected":
            log.info("와이파이 이미 연결됨 (%s)", parts[0])
            return True
    return False


def connect_wifi(ssid: str, password: str) -> tuple[bool, str]:
    """nmcli 로 와이파이 연결 시도. (성공 여부, 메시지) 반환.

    'nmcli device wifi connect' 는 스캔 캐시에 SSID 가 없으면 보안 방식
    (key-mgmt)을 알 수 없어 실패하므로, 보안 방식을 명시한 연결 프로필을
    직접 만들어 활성화한다. 숨김 SSID 도 함께 지원된다.
    """
    log.info("와이파이 연결 시도: %r", ssid)

    # 같은 이름의 이전 프로필이 있으면 제거 (재시도 시 잔여 설정 충돌 방지)
    subprocess.run(
        ["nmcli", "connection", "delete", ssid],
        capture_output=True, text=True, timeout=15,
    )

    cmd = [
        "nmcli", "connection", "add", "type", "wifi",
        "ifname", settings.wifi_interface,
        "con-name", ssid,
        "ssid", ssid,
        "connection.autoconnect", "yes",
        # 스캔에 안 잡혀도(숨김 포함) SSID 를 직접 지정해 접속하도록
        "802-11-wireless.hidden", "yes",
    ]
    if password:
        cmd += [
            "802-11-wireless-security.key-mgmt", "wpa-psk",
            "802-11-wireless-security.psk", password,
        ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            log.error("프로필 생성 실패: %s", detail)
            return False, detail

        up = subprocess.run(
            ["nmcli", "connection", "up", ssid],
            capture_output=True, text=True, timeout=90,
        )
    except subprocess.TimeoutExpired:
        return False, "nmcli timeout"

    if up.returncode != 0:
        detail = (up.stderr or up.stdout).strip()
        log.error("와이파이 연결 실패: %s", detail)
        # 실패한 프로필은 남겨두지 않는다 (autoconnect 로 재시도 반복 방지)
        subprocess.run(["nmcli", "connection", "delete", ssid],
                       capture_output=True, text=True, timeout=15)
        return False, detail

    log.info("와이파이 연결 성공: %r", ssid)
    return True, f"connected to {ssid}"


# ------------------------------------------------------------------ #
# BLE GATT 서버
# ------------------------------------------------------------------ #
class WifiProvisioner:
    def __init__(self) -> None:
        self._server: BlessServer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._buffer = bytearray()          # '\n' 까지 수신 데이터 누적
        self._connected = asyncio.Event()   # 와이파이 연결 성공 시 set
        self._busy = False                  # BLE 로 받은 자격증명으로 연결 시도 중

    # --- BLE 콜백 (bless 이벤트 루프에서 호출) --------------------- #
    def _on_read(self, characteristic: BlessGATTCharacteristic, **kwargs) -> bytearray:
        if characteristic is None or not characteristic.value:
            return bytearray(b'{"status": "waiting"}')
        return characteristic.value

    def _on_write(self, characteristic: BlessGATTCharacteristic, value: bytearray, **kwargs) -> None:
        if characteristic is None or characteristic.uuid.lower() != CREDS_CHAR_UUID:
            return
        self._buffer.extend(value)
        if b"\n" not in self._buffer:
            return  # 아직 조각 수신 중

        raw, _, rest = bytes(self._buffer).partition(b"\n")
        self._buffer = bytearray(rest)

        try:
            creds = json.loads(raw.decode("utf-8"))
            ssid = str(creds["ssid"])
            password = str(creds.get("password", ""))
        except (ValueError, KeyError) as exc:
            log.error("잘못된 자격증명 페이로드: %s", exc)
            self._notify_status("failed", f"bad payload: {exc}")
            return

        log.info("와이파이 자격증명 수신: ssid=%r", ssid)
        assert self._loop is not None
        self._loop.create_task(self._connect_and_report(ssid, password))

    # --- 와이파이 연결 → 결과 회신 --------------------------------- #
    async def _connect_and_report(self, ssid: str, password: str) -> None:
        # 시도 중에는 watchdog 이 먼저 종료시키지 못하게 막는다.
        # (연결 성공 직후 watchdog 이 _connected 를 set 하면 'connected' 회신
        #  notify 가 나가기 전에 BLE 서버가 꺼져 노트북이 실패로 오인한다)
        self._busy = True
        try:
            self._notify_status("connecting", f"trying {ssid}")
            # nmcli 는 블로킹이므로 스레드로 분리 (BLE 응답이 멈추지 않도록)
            ok, detail = await asyncio.to_thread(connect_wifi, ssid, password)
            if ok:
                self._notify_status("connected", detail)
                # 노트북이 notify 를 받을 시간을 준 뒤 종료
                await asyncio.sleep(2)
                self._connected.set()
            else:
                self._notify_status("failed", detail)  # 노트북이 재전송 가능
        finally:
            self._busy = False

    def _notify_status(self, status: str, detail: str = "") -> None:
        payload = json.dumps({"status": status, "detail": detail}).encode("utf-8")
        assert self._server is not None
        char = self._server.get_characteristic(STATUS_CHAR_UUID)
        if char is not None:
            char.value = bytearray(payload)
            self._server.update_value(SERVICE_UUID, STATUS_CHAR_UUID)
        log.info("상태 회신: %s (%s)", status, detail)

    # --- 와이파이 자동 연결 감시 ------------------------------------ #
    async def _wifi_watchdog(self, interval: float = 10.0) -> None:
        """BLE 대기 중에도 주기적으로 와이파이 상태를 확인.

        NetworkManager 가 저장된 와이파이에 뒤늦게 스스로 연결하면
        프로비저닝 없이도 빠져나가 MQTT 로 진행할 수 있도록 한다.
        """
        while not self._connected.is_set():
            await asyncio.sleep(interval)
            if self._busy:
                continue  # BLE 프로비저닝 진행 중 — 회신은 _connect_and_report 가 담당
            if await asyncio.to_thread(wifi_is_connected) and not self._busy:
                log.info("와이파이가 자동으로 연결됨 — 프로비저닝 종료")
                self._connected.set()

    # --- 실행 ------------------------------------------------------ #
    async def run(self) -> None:
        """BLE 서버를 띄우고 와이파이 연결 성공까지 대기."""
        self._loop = asyncio.get_running_loop()
        self._server = BlessServer(name=settings.ble_name, loop=self._loop)
        self._server.read_request_func = self._on_read
        self._server.write_request_func = self._on_write

        await self._server.add_new_service(SERVICE_UUID)
        await self._server.add_new_characteristic(
            SERVICE_UUID,
            CREDS_CHAR_UUID,
            GATTCharacteristicProperties.write,
            None,
            GATTAttributePermissions.writeable,
        )
        await self._server.add_new_characteristic(
            SERVICE_UUID,
            STATUS_CHAR_UUID,
            GATTCharacteristicProperties.read | GATTCharacteristicProperties.notify,
            bytearray(b'{"status": "waiting"}'),
            GATTAttributePermissions.readable,
        )

        await self._server.start()
        log.info("BLE 프로비저닝 대기 중 — 기기 이름: %r", settings.ble_name)
        watchdog = asyncio.create_task(self._wifi_watchdog())
        try:
            await self._connected.wait()
        finally:
            watchdog.cancel()
            await self._server.stop()
            log.info("BLE 서버 종료")


def ensure_wifi(boot_wait: float = 15.0) -> None:
    """와이파이가 안 붙어 있으면 BLE 프로비저닝으로 연결될 때까지 블로킹.

    부팅 직후에는 NetworkManager 가 저장된 와이파이에 붙는 데 몇 초 걸리므로,
    BLE 를 띄우기 전에 boot_wait 초 동안 자동 연결을 재확인한다.
    """
    if wifi_is_connected():
        return

    log.info("와이파이 미연결 — 자동 연결을 최대 %.0f초 대기", boot_wait)
    deadline = time.monotonic() + boot_wait
    while time.monotonic() < deadline:
        time.sleep(3)
        if wifi_is_connected():
            return

    log.info("자동 연결 안 됨 — BLE 프로비저닝 시작")
    asyncio.run(WifiProvisioner().run())


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )
    ensure_wifi()
    print("와이파이 연결 완료")
