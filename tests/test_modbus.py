import os
import unittest

# from modbus4mqtt.modbus4mqtt import mqtt_interface
from modbus4mqtt import modbus4mqtt, modbus_interface

class BasicTests(unittest.TestCase):

    def setUp(self):
        m = modbus4mqtt.mqtt_interface('localhost', 1885, 'username', 'password', './tests/test.yaml', 'test')
        m.connect()
        self.assertEqual(False, False)

    def tearDown(self):
        pass

    def test_main_page(self):
        self.assertEqual(200, 200)


if __name__ == "__main__":
    unittest.main()