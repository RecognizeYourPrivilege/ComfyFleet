import unittest

from comfyfleet.ports import PORT_START, choose_port


class PortTests(unittest.TestCase):
    def test_starts_at_8188(self):
        self.assertEqual(choose_port(set(), in_use=lambda _port: False), PORT_START)

    def test_skips_reserved_and_in_use(self):
        port = choose_port({8188}, in_use=lambda port: port == 8189)
        self.assertEqual(port, 8190)

    def test_prefers_recorded_port_when_free(self):
        port = choose_port(set(), preferred=8191, in_use=lambda port: port == 8188)
        self.assertEqual(port, 8191)

    def test_recorded_port_busy_moves_to_next_free(self):
        port = choose_port({8190}, preferred=8188, in_use=lambda port: port == 8188)
        self.assertEqual(port, 8189)


if __name__ == "__main__":
    unittest.main()
