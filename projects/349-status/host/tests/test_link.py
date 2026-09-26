from status349.link import Link
from status349.proto import classify, encode


def test_encode_prefix_and_newline():
    assert encode({"t": "ping", "ts": 1}) == b'@349 {"t":"ping","ts":1}\n'


def test_classify_data_line():
    assert classify('@349 {"t":"pong","ts":1}') == (True, {"t": "pong", "ts": 1})


def test_classify_log_line():
    assert classify("I (123) boot: hello") == (False, None)


def test_classify_malformed_json():
    assert classify("@349 nope") == (True, None)


def test_classify_non_object():
    assert classify("@349 [1,2]") == (True, None)


def test_link_lines_drains_buffered_line_before_reading_again():
    class FakePort:
        def __init__(self):
            self.timeout = None
            self.reads = 0
            self.chunks = [b"first\r\nsecond\n"]

        def read(self, _size):
            self.reads += 1
            return self.chunks.pop(0) if self.chunks else b""

    port = FakePort()
    link = Link(port)

    first = link.lines(timeout=0.2)
    assert next(first) == "first"
    first.close()

    second = link.lines(timeout=0.2)
    assert next(second) == "second"
    second.close()
    assert port.reads == 1
