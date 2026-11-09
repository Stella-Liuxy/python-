from io import BytesIO
from threading import Event
import unittest
from unittest.mock import patch

from agent.model import ChatModel


class ChatModelTests(unittest.TestCase):
    def setUp(self):
        self.model = ChatModel("http://127.0.0.1/v1", "test-only", "test")

    @patch("agent.model.build_opener")
    def test_reads_complete_response(self, make_opener):
        make_opener.return_value.open.return_value = BytesIO(
            b'{"choices":[{"message":{"content":"ok"}}]}'
        )
        self.assertEqual(self.model.respond([], [], Event()), {"content": "ok"})

    @patch("agent.model.REQUEST_TIMEOUT", 0.05)
    @patch("agent.model.build_opener")
    def test_wait_has_deadline_even_when_transport_has_not_returned(self, make_opener):
        release = Event()
        self.addCleanup(release.set)

        def pending_open(*args, **kwargs):
            if not release.wait(2):
                raise OSError("Test transport was not released")
            return BytesIO()

        make_opener.return_value.open.side_effect = pending_open
        with self.assertRaisesRegex(ValueError, "模型请求超时"):
            self.model.respond([], [], Event())

    @patch("agent.model.MAX_RESPONSE", 8)
    @patch("agent.model.build_opener")
    def test_rejects_oversized_response(self, make_opener):
        make_opener.return_value.open.return_value = BytesIO(b"123456789")
        with self.assertRaisesRegex(ValueError, "模型响应超过"):
            self.model.respond([], [], Event())
