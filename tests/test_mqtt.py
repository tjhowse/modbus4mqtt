import os
import unittest
from unittest.mock import patch, call, Mock

from modbus4mqtt import modbus4mqtt

# class mocked_mqtt_client():


class BasicTests(unittest.TestCase):

    def setUp(self):
        # mock = unittest.mock.MagicMock()
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                m = modbus4mqtt.mqtt_interface('localhost', 1885, 'username', 'password', './tests/test.yaml', 'test')
                m.connect()
                calls = [   call(),
                            call().username_pw_set('username', 'password'),
                            call().connect('localhost', 1885, 60),
                            call().loop_start()
                        ]
                mock_mqtt.assert_has_calls(calls)
                # TODO Work out why this isn't working properly.
                mock_modbus.get_value.return_value = 85
                m.poll()
                print(mock_mqtt.mock_calls)
                print(mock_modbus.mock_calls)

    def tearDown(self):
        pass

    def test_main_page(self):
        self.assertEqual(200, 200)


if __name__ == "__main__":
    unittest.main()