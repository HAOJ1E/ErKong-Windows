"""研究模块的协议边界与 Windows 管道测试，不产生耳机运动数据。"""

import os
import struct
import threading
import unittest
import uuid
from multiprocessing.connection import Listener
from unittest.mock import patch

from nodlink.aap_motion import candidate_motion_fields
from nodlink.sdp_records import l2cap_psms
from nodlink.wpods_transport import MessagePipe, WPodsTransport, bridge_availability


class ProtocolTests(unittest.TestCase):
    def test_sdp_truncation_and_attribute_scope(self):
        record = bytes.fromhex("35 0D 09 00 04 35 08 35 06 19 01 00 09 10 01")
        self.assertEqual(l2cap_psms(record), {0x1001})
        for length in range(len(record)):
            with self.assertRaises(ValueError):
                l2cap_psms(record[:length])
        different_attribute = record.replace(b"\x09\x00\x04", b"\x09\x00\x05")
        self.assertEqual(l2cap_psms(different_attribute), set())

    def test_candidate_layout_rejects_other_packets(self):
        self.assertIsNone(candidate_motion_fields(bytes(100)))
        # 这只是人为构造的字节布局测试，不是采集数据或兼容性验证。
        payload = bytearray(58)
        payload[0], payload[9] = 1, 3
        struct.pack_into("<hhh", payload, 20, -32768, 0, 32767)
        struct.pack_into("<hh", payload, 28, -1, 1)
        command = bytes.fromhex("08 10 1A 3A") + payload
        body = bytes.fromhex("08 01 10 03 3A 3E") + command
        packet = bytearray(bytes.fromhex("04 00 04 00 17 00 00 00 10 00") + struct.pack("<H", len(body)) + body)
        fields = candidate_motion_fields(packet)
        self.assertEqual(fields["orientation_raw"], [-32768, 0, 32767])
        self.assertEqual(fields["acceleration_raw"], [-1, 1])
        self.assertIn("unverified", fields["layout"])
        self.assertEqual(fields["service"], 16)
        for length in range(len(packet)):
            self.assertIsNone(candidate_motion_fields(packet[:length]))
        packet[10] = 0x30
        self.assertIsNone(candidate_motion_fields(packet))


class BridgeStateTests(unittest.TestCase):
    def test_restart_marker_prevents_pipe_access(self):
        state = {"installed": True, "running": False, "process_id": 0}
        with patch("nodlink.wpods_transport.service_state", return_value=state), patch(
            "nodlink.wpods_transport.winreg.OpenKey"
        ), patch("nodlink.wpods_transport.winreg.QueryValueEx", return_value=(1, 4)), patch(
            "nodlink.wpods_transport._winapi.WaitNamedPipe"
        ) as wait_pipe:
            result = bridge_availability()
            self.assertEqual(result["status"], "restart_required")
            wait_pipe.assert_not_called()

    def test_registry_error_is_reported(self):
        state = {"installed": False, "running": False, "process_id": None}
        with patch("nodlink.wpods_transport.service_state", return_value=state), patch(
            "nodlink.wpods_transport.winreg.OpenKey", side_effect=PermissionError("test only")
        ):
            result = bridge_availability()
            self.assertEqual(result["status"], "not_installed")
            self.assertIn("restart_marker_error", result)


class NativePipeTests(unittest.TestCase):
    def setUp(self):
        # 测试使用随机独立管道名称，从不冒充真实 WPods 服务。
        self.name = r"\\.\pipe\NodLink.TestOnly." + uuid.uuid4().hex
        self.listener = Listener(self.name, family="AF_PIPE")
        self.server_errors = []

    def tearDown(self):
        self.listener.close()

    def serve(self, handler):
        def run():
            try:
                with self.listener.accept() as connection:
                    handler(connection)
            except BaseException as error:
                self.server_errors.append(error)

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread

    def finish_server(self, thread):
        thread.join(3.0)
        self.assertFalse(thread.is_alive(), "Test pipe thread did not finish")
        self.assertEqual(self.server_errors, [])

    def test_messages_keep_boundaries(self):
        def server(connection):
            self.assertEqual(connection.recv_bytes(), b"FIRST TEST MESSAGE")
            self.assertEqual(connection.recv_bytes(), b"SECOND TEST MESSAGE")
            connection.send_bytes(b"TEST ONLY " * 40)
            connection.send_bytes(b"TEST END")

        thread = self.serve(server)
        pipe = MessagePipe(self.name, os.getpid())
        try:
            pipe.write(b"FIRST TEST MESSAGE")
            pipe.write(b"SECOND TEST MESSAGE")
            self.assertEqual(pipe.read(1), b"TEST ONLY " * 40)
            self.assertEqual(pipe.read(1), b"TEST END")
        finally:
            pipe.close()
        self.finish_server(thread)

    def test_cancelled_read_does_not_consume_next_message(self):
        resume = threading.Event()

        def server(connection):
            self.assertTrue(resume.wait(2))
            connection.send_bytes(b"MESSAGE AFTER CANCELLED READ")

        thread = self.serve(server)
        pipe = MessagePipe(self.name, os.getpid())
        try:
            with self.assertRaises(TimeoutError):
                pipe.read(0.05)
            resume.set()
            self.assertEqual(pipe.read(1), b"MESSAGE AFTER CANCELLED READ")
        finally:
            resume.set()
            pipe.close()
        self.finish_server(thread)

    def test_reject_wrong_server_process(self):
        thread = self.serve(lambda connection: None)
        with self.assertRaises(PermissionError):
            MessagePipe(self.name, os.getpid() + 1)
        self.finish_server(thread)

    def test_reject_oversized_message(self):
        thread = self.serve(lambda connection: connection.send_bytes(b"X" * 2000))
        pipe = MessagePipe(self.name, os.getpid())
        try:
            with self.assertRaises(OSError) as raised:
                pipe.read(1)
            self.assertEqual(raised.exception.winerror, 234)  # ERROR_MORE_DATA
        finally:
            pipe.close()
        self.finish_server(thread)

    def test_bridge_busy_response_is_not_connection_success(self):
        def server(connection):
            hello = connection.recv_bytes()
            self.assertEqual(hello, b"AP" + struct.pack("<Q", 0x123456789ABC))
            connection.send_bytes(b"\x02" + struct.pack("<I", 170))

        thread = self.serve(server)
        state = {"installed": True, "running": True, "process_id": os.getpid()}
        with patch("nodlink.wpods_transport.PIPE_NAME", self.name), patch(
            "nodlink.wpods_transport.service_state", return_value=state
        ):
            with self.assertRaisesRegex(RuntimeError, "170"):
                WPodsTransport(0x123456789ABC)
        self.finish_server(thread)


if __name__ == "__main__":
    unittest.main()
