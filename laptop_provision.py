"""노트북에서 라즈베리파이로 와이파이 정보를 BLE 로 전송하는 스크립트.

라즈베리파이 쪽 wifi_provision.py 와 짝을 이룬다.
macOS / Windows / Linux 에서 동작 (bleak 사용).

설치:
  pip install bleak

사용:
  python laptop_provision.py --ssid "MyWifi" --password "secret123"
  python laptop_provision.py --ssid "MyWifi" --password "secret123" --name raspi-cam-setup
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys

from bleak import BleakClient, BleakScanner

# 라즈베리파이 쪽(wifi_provision.py)과 반드시 동일해야 한다.
# 환경변수로 덮어쓸 수 있다 (라즈베리파이 .env 와 같은 이름).
SERVICE_UUID = os.getenv(
    "SERVICE_UUID", "8e0d0001-7d4f-4f2a-9a6b-3c1f2a5d9e10").lower()
CREDS_CHAR_UUID = os.getenv(
    "CREDS_CHAR_UUID", "8e0d0002-7d4f-4f2a-9a6b-3c1f2a5d9e10").lower()
STATUS_CHAR_UUID = os.getenv(
    "STATUS_CHAR_UUID", "8e0d0003-7d4f-4f2a-9a6b-3c1f2a5d9e10").lower()

CHUNK_SIZE = 100          # BLE MTU 보다 넉넉히 작게 잘라 전송
RESULT_TIMEOUT = 150      # 와이파이 연결 결과 대기 시간(초, 파이 쪽 최악 소요보다 여유 있게)


async def find_device(name: str, timeout: float = 15.0):
    print(f"[스캔] BLE 기기 {name!r} 검색 중… (최대 {timeout:.0f}초)")
    device = await BleakScanner.find_device_by_name(name, timeout=timeout)
    if device is None:
        print(f"[오류] {name!r} 기기를 찾지 못했습니다. 라즈베리파이에서 "
              f"프로비저닝이 실행 중인지 확인하세요.", file=sys.stderr)
        sys.exit(1)
    print(f"[스캔] 발견: {device.name} ({device.address})")
    return device


async def provision(name: str, address: str | None, ssid: str, password: str) -> None:
    if address:
        target = address
    else:
        target = await find_device(name)

    result: asyncio.Future[dict] = asyncio.get_running_loop().create_future()

    def on_status(_, data: bytearray) -> None:
        try:
            msg = json.loads(bytes(data).decode("utf-8"))
        except ValueError:
            return
        status = msg.get("status")
        print(f"[상태] {status}: {msg.get('detail', '')}")
        if status in ("connected", "failed") and not result.done():
            result.set_result(msg)

    async with BleakClient(target) as client:
        print("[연결] BLE 연결됨 — 상태 알림 구독")
        await client.start_notify(STATUS_CHAR_UUID, on_status)

        payload = json.dumps({"ssid": ssid, "password": password}).encode("utf-8") + b"\n"
        print(f"[전송] 와이파이 정보 전송 (ssid={ssid!r}, {len(payload)} bytes)")
        for i in range(0, len(payload), CHUNK_SIZE):
            await client.write_gatt_char(
                CREDS_CHAR_UUID, payload[i:i + CHUNK_SIZE], response=True
            )

        print(f"[대기] 라즈베리파이의 와이파이 연결 결과 대기… (최대 {RESULT_TIMEOUT}초)")
        try:
            msg = await asyncio.wait_for(result, timeout=RESULT_TIMEOUT)
        except asyncio.TimeoutError:
            print("[오류] 결과 응답이 없습니다 (타임아웃).", file=sys.stderr)
            sys.exit(1)

    if msg["status"] == "connected":
        print("✅ 라즈베리파이 와이파이 연결 성공!")
    else:
        print(f"❌ 와이파이 연결 실패: {msg.get('detail', '')}", file=sys.stderr)
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="라즈베리파이 BLE 와이파이 프로비저닝")
    parser.add_argument("--ssid", required=True, help="와이파이 이름(SSID)")
    parser.add_argument("--password", default="", help="와이파이 비밀번호 (개방형이면 생략)")
    parser.add_argument("--name", default="raspi-cam-setup",
                        help="라즈베리파이 BLE 광고 이름 (기본: raspi-cam-setup)")
    parser.add_argument("--address", default=None,
                        help="기기 주소를 알면 스캔 생략 (예: XX:XX:XX:XX:XX:XX)")
    args = parser.parse_args()

    asyncio.run(provision(args.name, args.address, args.ssid, args.password))


if __name__ == "__main__":
    main()
