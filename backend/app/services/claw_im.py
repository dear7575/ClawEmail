import asyncio
import base64
import hashlib
import json
import logging
import struct
import time
from dataclasses import dataclass
from typing import Any

import httpx
import websockets
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat


logger = logging.getLogger(__name__)
IM_TOKEN_URL = "https://claw.163.com/claw-api-gateway/open/v1/mail/auth/im-token"
DEFAULT_WS_URL = "wss://claw.126.net:5210"
MAIL_PUSH_TYPE = 3001
HEARTBEAT_SECONDS = 60
MAX_MISSED_PONGS = 3


@dataclass
class ConnAckPacket:
    """IM 服务握手确认包。"""

    reason_code: int
    server_version: int
    time_diff: int
    server_key: str
    salt: str


@dataclass
class RecvPacket:
    """IM 服务新消息推送包。"""

    message_id: int
    message_seq: int
    from_uid: str
    channel_id: str
    channel_type: int
    payload: bytes


@dataclass
class DisconnectPacket:
    """IM 服务主动断开包。"""

    reason_code: int
    reason: str


DecodedPacket = ConnAckPacket | RecvPacket | DisconnectPacket | str


class PacketReader:
    """读取 Claw IM 二进制包的顺序游标。"""

    def __init__(self, data: bytes) -> None:
        """初始化二进制读取游标。"""

        self.data = data
        self.offset = 0

    def read_byte(self) -> int:
        """读取单字节整数。"""

        value = self.data[self.offset]
        self.offset += 1
        return value

    def read_int16(self) -> int:
        """读取网络序 16 位整数。"""

        value = struct.unpack_from(">H", self.data, self.offset)[0]
        self.offset += 2
        return value

    def read_int32(self) -> int:
        """读取网络序 32 位整数。"""

        value = struct.unpack_from(">I", self.data, self.offset)[0]
        self.offset += 4
        return value

    def read_int64(self) -> int:
        """读取网络序 64 位整数。"""

        value = struct.unpack_from(">Q", self.data, self.offset)[0]
        self.offset += 8
        return value

    def read_string(self) -> str:
        """读取 16 位长度前缀字符串。"""

        length = self.read_int16()
        if length <= 0:
            return ""
        raw = self.data[self.offset:self.offset + length]
        self.offset += length
        return raw.decode("utf-8")

    def read_varint(self) -> int:
        """读取协议包长度使用的 varint。"""

        value = 0
        shift = 0
        while shift < 27:
            byte = self.read_byte()
            value |= (byte & 0x7F) << shift
            if byte & 0x80 == 0:
                break
            shift += 7
        return value

    def read_remaining(self) -> bytes:
        """读取剩余全部字节。"""

        raw = self.data[self.offset:]
        self.offset = len(self.data)
        return raw


def base64_url(value: str) -> str:
    """把 uid 转为协议要求的 URL 安全 Base64。"""

    if not value:
        return value
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def encode_varint(value: int) -> bytes:
    """编码协议包长度。"""

    output = bytearray()
    while value > 0:
        byte = value % 128
        value //= 128
        if value > 0:
            byte |= 0x80
        output.append(byte)
    return bytes(output or b"\x00")


def encode_string(value: str) -> bytes:
    """编码 16 位长度前缀字符串。"""

    raw = value.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def frame_packet(packet_type: int, payload: bytes) -> bytes:
    """给 IM 协议 payload 添加包类型和长度头。"""

    return bytes([packet_type << 4]) + encode_varint(len(payload)) + payload


def encode_connect(
    uid: str,
    token: str,
    device_id: str,
    public_key: str,
    timestamp_ms: int | None = None,
) -> bytes:
    """编码 CONNECT 握手包。"""

    payload = bytearray()
    payload.append(4)
    payload.append(1)
    payload.extend(encode_string(device_id))
    payload.extend(encode_string(base64_url(uid)))
    payload.extend(encode_string(token))
    payload.extend(struct.pack(">Q", timestamp_ms or int(time.time() * 1000)))
    payload.extend(encode_string(public_key))
    return frame_packet(1, bytes(payload))


def encode_ping() -> bytes:
    """编码心跳 PING 包。"""

    return b"\x70"


def encode_recvack(message_id: int, message_seq: int) -> bytes:
    """编码消息确认 ACK 包。"""

    return frame_packet(6, struct.pack(">QI", message_id, message_seq))


def encode_disconnect() -> bytes:
    """编码主动断开包。"""

    payload = b"\x00" + encode_string("")
    return frame_packet(9, payload)


def decode_packet(raw: bytes) -> DecodedPacket:
    """解码 Claw IM 二进制包。"""

    first = raw[0]
    packet_type = (first >> 4) & 0x0F
    if packet_type in {7, 8}:
        return "pong"
    reader = PacketReader(raw)
    reader.read_byte()
    reader.read_varint()
    if packet_type == 2:
        has_server_version = (first & 1) > 0
        server_version = reader.read_byte() if has_server_version else 0
        time_diff = reader.read_int64()
        reason_code = reader.read_byte()
        server_key = reader.read_string()
        salt = reader.read_string()
        if server_version >= 4:
            reader.read_int64()
        return ConnAckPacket(
            reason_code=reason_code,
            server_version=server_version,
            time_diff=time_diff,
            server_key=server_key,
            salt=salt,
        )
    if packet_type == 5:
        reader.read_byte()
        reader.read_string()
        from_uid = reader.read_string()
        channel_id = reader.read_string()
        channel_type = reader.read_byte()
        reader.read_int32()
        reader.read_string()
        message_id = reader.read_int64()
        message_seq = reader.read_int32()
        reader.read_int32()
        return RecvPacket(
            message_id=message_id,
            message_seq=message_seq,
            from_uid=from_uid,
            channel_id=channel_id,
            channel_type=channel_type,
            payload=reader.read_remaining(),
        )
    if packet_type == 9:
        reason_code = reader.read_byte()
        return DisconnectPacket(reason_code=reason_code, reason=reader.read_string())
    return f"unknown:{packet_type}"


def decrypt_payload(payload: bytes, aes_key: bytes, aes_iv: bytes) -> bytes:
    """解密 IM RECV 包中的 AES-CBC 载荷。"""

    encrypted = base64.b64decode(payload.decode("utf-8"))
    decryptor = Cipher(algorithms.AES(aes_key), modes.CBC(aes_iv)).decryptor()
    padded = decryptor.update(encrypted) + decryptor.finalize()
    unpadder = PKCS7(128).unpadder()
    return unpadder.update(padded) + unpadder.finalize()


def device_id_for_uid(uid: str) -> str:
    """复刻旧 SDK 的稳定设备 ID 生成规则。"""

    value = 0
    for char in uid:
        value = ((value << 5) - value + ord(char)) & 0xFFFFFFFF
        if value >= 0x80000000:
            value -= 0x100000000
    return f"claw-cli-{abs(value):08x}"


async def fetch_im_token(api_key: str, uid: str, timeout: float = 15.0) -> str:
    """获取 Claw IM WebSocket 专用 token。

    参数:
        api_key: Claw Open API Key。
        uid: Claw 邮箱地址。
        timeout: HTTP 超时时间，单位秒。

    返回:
        IM WebSocket 握手 token。

    异常:
        RuntimeError: IM token 接口失败或响应缺少 token。
    """

    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        response = await client.post(
            IM_TOKEN_URL,
            headers={"authorization": f"Bearer {api_key}", "content-type": "application/json"},
            json={"uid": uid},
        )
    try:
        body = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Claw IM token 接口返回非 JSON：HTTP {response.status_code}") from exc
    if not response.is_success or body.get("success") is not True:
        raise RuntimeError(f"Claw IM token 获取失败：{body}")
    token = body.get("result", {}).get("accessToken") if isinstance(body.get("result"), dict) else None
    if not isinstance(token, str) or not token.strip():
        raise RuntimeError(f"Claw IM token 响应缺少 accessToken：{body}")
    return token


class ClawImWebSocket:
    """Claw 邮箱 IM WebSocket 客户端。"""

    def __init__(self, uid: str, api_key: str, ws_url: str = DEFAULT_WS_URL) -> None:
        """初始化 WebSocket 客户端。"""

        self.uid = uid
        self.api_key = api_key
        self.ws_url = ws_url
        self.aes_key: bytes | None = None
        self.aes_iv: bytes | None = None

    async def connect(self):
        """连接 IM 服务并完成握手。"""

        token = await fetch_im_token(self.api_key, self.uid)
        private_key = x25519.X25519PrivateKey.generate()
        public_key = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        public_key_b64 = base64.b64encode(public_key).decode("ascii")
        websocket = await websockets.connect(self.ws_url, open_timeout=10)
        await websocket.send(encode_connect(self.uid, token, device_id_for_uid(self.uid), public_key_b64))
        raw = await asyncio.wait_for(websocket.recv(), timeout=10)
        packet = decode_packet(raw if isinstance(raw, bytes) else raw.encode("utf-8"))
        if not isinstance(packet, ConnAckPacket):
            await websocket.close()
            raise RuntimeError(f"Claw IM 握手失败：期待 CONNACK，实际 {packet}")
        if packet.reason_code != 1:
            await websocket.close()
            raise RuntimeError(f"Claw IM 握手被拒绝：reasonCode={packet.reason_code}")
        if packet.server_key:
            server_public_key = x25519.X25519PublicKey.from_public_bytes(base64.b64decode(packet.server_key))
            shared = private_key.exchange(server_public_key)
            digest = hashlib.md5(base64.b64encode(shared)).hexdigest()
            self.aes_key = digest[:16].encode("utf-8")
            self.aes_iv = packet.salt[:16].encode("utf-8")
        return websocket

    def decode_push(self, packet: RecvPacket) -> dict[str, Any] | None:
        """解密并解析新邮件推送载荷。"""

        payload = packet.payload
        if self.aes_key and self.aes_iv:
            payload = decrypt_payload(payload, self.aes_key, self.aes_iv)
        try:
            data = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            logger.warning("Claw IM 推送载荷不是 JSON：uid=%s bytes=%s", self.uid, len(payload))
            return None
        if isinstance(data, dict) and data.get("type") == MAIL_PUSH_TYPE and isinstance(data.get("mailId"), str):
            return data
        return None
