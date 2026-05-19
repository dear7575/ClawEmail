import json

import pytest

from app.services.claw_im import (
    MAIL_PUSH_TYPE,
    RecvPacket,
    ClawImWebSocket,
    base64_url,
    device_id_for_uid,
    encode_connect,
    encode_recvack,
)
from app.services.listeners import ListenerManager, ListenerState


def test_claw_im_encoding_matches_sdk_wire_shape() -> None:
    packet = encode_connect(
        uid="root@claw.163.com",
        token="im-token",
        device_id="device-1",
        public_key="public-key",
        timestamp_ms=1,
    )

    assert base64_url("root@claw.163.com") == "cm9vdEBjbGF3LjE2My5jb20"
    assert packet[0] == 0x10
    assert b"device-1" in packet
    assert b"cm9vdEBjbGF3LjE2My5jb20" in packet
    assert b"im-token" in packet
    assert encode_recvack(1, 2) == b"\x60\x0c\x00\x00\x00\x00\x00\x00\x00\x01\x00\x00\x00\x02"


def test_device_id_is_stable_for_mailbox_uid() -> None:
    assert device_id_for_uid("root@claw.163.com") == device_id_for_uid("root@claw.163.com")
    assert device_id_for_uid("root@claw.163.com").startswith("claw-cli-")


def test_decode_push_accepts_mail_push_json_only() -> None:
    client = ClawImWebSocket("root@claw.163.com", "api-key")
    push_packet = RecvPacket(
        message_id=1,
        message_seq=1,
        from_uid="server",
        channel_id="root@claw.163.com",
        channel_type=1,
        payload=json.dumps({"type": MAIL_PUSH_TYPE, "mailId": "mail-1"}).encode("utf-8"),
    )
    ignored_packet = RecvPacket(
        message_id=2,
        message_seq=1,
        from_uid="server",
        channel_id="root@claw.163.com",
        channel_type=1,
        payload=json.dumps({"type": 1, "mailId": "mail-2"}).encode("utf-8"),
    )

    assert client.decode_push(push_packet) == {"type": MAIL_PUSH_TYPE, "mailId": "mail-1"}
    assert client.decode_push(ignored_packet) is None


class FakeMailRepository:
    def __init__(self) -> None:
        self.saved: list[dict] = []

    def save_mail(self, input_value: dict) -> dict:
        self.saved.append(input_value)
        return {"id": 42, **input_value}


class FakeMailClient:
    def read_mail(self, mailbox_email: str, provider_mail_id: str, connection_id: str, mark_read: bool) -> dict:
        return {
            "id": provider_mail_id,
            "from": ["sender@example.com"],
            "to": [mailbox_email],
            "subject": "hello",
            "text": {"content": "body"},
            "html": None,
            "date": "2026-05-18T00:00:00",
            "attachments": [],
        }


@pytest.mark.asyncio
async def test_listener_persists_mail_push_and_broadcasts_sse() -> None:
    repository = FakeMailRepository()
    manager = ListenerManager(mail_repository=repository, mail_client=FakeMailClient())
    state = ListenerState(connection_id="conn-1", email="root@claw.163.com")

    await manager._persist_mail_push(state, "mail-1")
    manager.shutdown()

    assert repository.saved[0]["connection_id"] == "conn-1"
    assert repository.saved[0]["provider_mail_id"] == "mail-1"
    assert repository.saved[0]["mailbox_email"] == "root@claw.163.com"
    assert repository.saved[0]["subject"] == "hello"
