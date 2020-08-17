import os
import unittest
from unittest.mock import patch, call, Mock
from paho.mqtt.client import MQTTMessage

from modbus4mqtt import modbus4mqtt

from click.testing import CliRunner

def assert_no_call(self, *args, **kwargs):
    try:
        self.assert_any_call(*args, **kwargs)
    except AssertionError:
        return
    raise AssertionError('Expected %s to not have been called.' % self._format_mock_call_signature(args, kwargs))

Mock.assert_no_call = assert_no_call

MQTT_TOPIC_PREFIX = 'prefix'

class MQTTTests(unittest.TestCase):

    def setUp(self):
        self.modbus_tables = {'input': {}, 'holding': {}}
        self.connect_attempts = 0

    def tearDown(self):
        pass

    def read_modbus_register(self, table, address):
        if address not in self.modbus_tables[table]:
            raise ValueError("Invalid address")
        return self.modbus_tables[table][address]

    def write_modbus_register(self, table, address, value, mask=0xFFFF):
        old_value = self.modbus_tables[table][address]
        and_mask = (1<<16)-1-mask
        or_mask = value
        new_value = (old_value & and_mask) | (or_mask & (mask))
        self.modbus_tables[table][address] = new_value

    def connect_success(self):
        self.connect_attempts += 1
        return False

    def connect_failure(self):
        self.connect_attempts += 1
        return True

    def test_main(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus4mqtt.mqtt_interface.loop_forever') as mock_mainloop:
                with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                    mock_modbus().connect.side_effect = self.connect_success
                    runner = CliRunner()
                    args = []
                    args += ['--hostname', 'kroopit']
                    args += ['--port', '1885']
                    args += ['--username', 'brengis']
                    args += ['--password', 'pranto']
                    args += ['--config', './tests/test_connect.yaml']
                    args += ['--mqtt_topic_prefix', MQTT_TOPIC_PREFIX]

                    runner.invoke(modbus4mqtt.main, args)
                    mock_mainloop.assert_called_with()

                    mock_mqtt().username_pw_set.assert_called_with('brengis', 'pranto')
                    mock_mqtt().connect.assert_called_with('kroopit', 1885, 60)

    def test_connect(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_connect.yaml', MQTT_TOPIC_PREFIX)
                m.connect()

                mock_mqtt().username_pw_set.assert_called_with('brengis', 'pranto')
                mock_mqtt().connect.assert_called_with('kroopit', 1885, 60)

                m._on_connect(None, None, None, rc=0)
                mock_mqtt().publish.assert_called_with(MQTT_TOPIC_PREFIX+'/modbus4mqtt', 'hi')
                mock_mqtt().subscribe.assert_called_with(MQTT_TOPIC_PREFIX+'/subscribe')
                mock_mqtt().subscribe.assert_no_call(MQTT_TOPIC_PREFIX+'/publish')

    def test_failed_modbus_connect(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_failure
                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_connect.yaml', MQTT_TOPIC_PREFIX)
                self.connect_attempts = 0
                m.modbus_connect_retries = 3
                m.modbus_reconnect_sleep_interval = 0.1
                def replacement():
                    # Normally this would kill the program. We don't want that.
                    pass
                m.modbus_connection_failed = replacement
                m.connect()
                self.assertEqual(self.connect_attempts, 3)

                mock_mqtt().username_pw_set.assert_called_with('brengis', 'pranto')
                mock_mqtt().connect.assert_called_with('kroopit', 1885, 60)
                m._on_connect(None, None, None, rc=1)
                # TODO implement some more thorough checks?
                mock_mqtt().publish.assert_no_call(MQTT_TOPIC_PREFIX+'/modbus4mqtt', 'hi')

    def test_pub_on_change(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success

                mock_modbus().get_value.side_effect = self.read_modbus_register

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_pub_on_change.yaml', MQTT_TOPIC_PREFIX)
                m.connect()

                self.modbus_tables['holding'][1] = 85
                self.modbus_tables['holding'][2] = 86
                self.modbus_tables['holding'][3] = 87
                m.poll()

                # Check that every topic was published initially
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_false', 85, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_true', 86, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_absent', 87, retain=False)
                mock_mqtt().publish.reset_mock()

                self.modbus_tables['holding'][1] = 15
                self.modbus_tables['holding'][2] = 16
                self.modbus_tables['holding'][3] = 17
                m.poll()

                # Check that every topic was published if everything changed
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_false', 15, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_true', 16, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_absent', 17, retain=False)
                mock_mqtt().publish.reset_mock()

                m.poll()

                # Check that the register with pub_only_on_change: true does not re-publish
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/pub_on_change_false', 15, retain=False)
                mock_mqtt().publish.assert_no_call(MQTT_TOPIC_PREFIX+'/pub_on_change_true', 16, retain=False)
                mock_mqtt().publish.assert_no_call(MQTT_TOPIC_PREFIX+'/pub_on_change_absent', 17, retain=False)

    def test_retain_flag(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_retain_flag.yaml', MQTT_TOPIC_PREFIX)
                m.connect()
                self.modbus_tables['holding'][1] = 1
                self.modbus_tables['holding'][2] = 2
                self.modbus_tables['holding'][3] = 3
                m.poll()

                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/retain_on', 1, retain=True)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/retain_off', 2, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/retain_absent', 3, retain=False)

    def test_default_table(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_default_table.yaml', MQTT_TOPIC_PREFIX)
                m.connect()
                self.modbus_tables['holding'][1] = 1
                self.modbus_tables['holding'][2] = 2
                self.modbus_tables['input'][1] = 3
                m.poll()

                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/holding', 1, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/input', 3, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/default', 2, retain=False)

    def test_value_map(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_value_map.yaml', MQTT_TOPIC_PREFIX)
                m.connect()
                self.modbus_tables['holding'][1] = 1
                self.modbus_tables['holding'][2] = 2
                m.poll()

                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/value_map_absent', 1, retain=False)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/value_map_present', 'b', retain=False)
                mock_mqtt().publish.reset_mock()

                # This value is outside the map, check it comes through in raw form
                self.modbus_tables['holding'][2] = 3
                m.poll()

                # print(mock_mqtt.mock_calls)
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/value_map_present', 3, retain=False)
                mock_mqtt().publish.reset_mock()

    def test_invalid_address(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_value_map.yaml', MQTT_TOPIC_PREFIX)
                m.connect()
                # Don't set up address 2, so the register polling it throws an exception
                self.modbus_tables['holding'][1] = 1
                m.poll()
                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/value_map_absent', 1, retain=False)
                mock_mqtt().publish.assert_no_call(MQTT_TOPIC_PREFIX+'/value_map_present', 'b', retain=False)

    def test_set_topics(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                with self.assertLogs() as mock_logger:
                    mock_modbus().get_value.side_effect = self.read_modbus_register
                    mock_modbus().set_value.side_effect = self.write_modbus_register
                    self.modbus_tables['holding'][1] = 1
                    self.modbus_tables['holding'][2] = 2

                    m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_set_topics.yaml', MQTT_TOPIC_PREFIX)
                    m.connect()

                    mock_modbus().add_monitor_register.assert_any_call('holding', 1)
                    mock_modbus().add_monitor_register.assert_any_call('holding', 2)

                    mock_mqtt().username_pw_set.assert_called_with('brengis', 'pranto')
                    mock_mqtt().connect.assert_called_with('kroopit', 1885, 60)

                    m._on_connect(None, None, None, rc=0)
                    mock_mqtt().subscribe.assert_any_call(MQTT_TOPIC_PREFIX+'/no_value_map')
                    mock_mqtt().subscribe.assert_any_call(MQTT_TOPIC_PREFIX+'/value_map')
                    mock_mqtt().publish.reset_mock()

                    self.assertEqual(self.modbus_tables['holding'][2], 2)
                    # Publish a human-readable value invalid for this topic, because there's no value map.
                    msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/no_value_map', 'utf-8'))
                    msg.payload = b'a'
                    m._on_message(None, None, msg)
                    self.assertIn("Failed to convert register value for writing. Bad/missing value_map? Topic: no_value_map, Value: b'a'", mock_logger.output[-1])
                    self.assertEqual(self.modbus_tables['holding'][2], 2)

                    self.assertEqual(self.modbus_tables['holding'][1], 1)
                    # Publish a raw value valid for this topic
                    msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/no_value_map', 'utf-8'))
                    msg.payload = b'3'
                    m._on_message(None, None, msg)
                    self.assertEqual(self.modbus_tables['holding'][1], 3)

                    # Publish a human-readable value valid for this topic, because there's a value map.
                    self.assertEqual(self.modbus_tables['holding'][2], 2)
                    msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/value_map', 'utf-8'))
                    msg.payload = b'a'
                    m._on_message(None, None, msg)
                    self.assertEqual(self.modbus_tables['holding'][2], 1)

                    # Publish a raw value invalid for this topic, ensure the value doesn't change.
                    msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/value_map', 'utf-8'))
                    msg.payload = bytes([3])
                    m._on_message(None, None, msg)
                    self.assertIn("Value not in value_map. Topic: value_map, value:", mock_logger.output[-1])
                    self.assertEqual(self.modbus_tables['holding'][2], 1)

                    # Publish a value that can't decode as utf-8, ensure the value doesn't change.
                    msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/value_map', 'utf-8'))
                    msg.payload = b'\xff'
                    m._on_message(None, None, msg)
                    self.assertIn("Failed to decode MQTT payload as UTF-8. Can't compare it to the value_map for register", mock_logger.output[-1])
                    self.assertEqual(self.modbus_tables['holding'][2], 1)


    def test_scale(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register
                mock_modbus().set_value.side_effect = self.write_modbus_register
                self.modbus_tables['holding'][1] = 1
                self.modbus_tables['holding'][2] = 2
                self.modbus_tables['holding'][3] = 3

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_scale.yaml', MQTT_TOPIC_PREFIX)
                m.connect()
                m.poll()

                mock_modbus().add_monitor_register.assert_any_call('holding', 1)
                mock_modbus().add_monitor_register.assert_any_call('holding', 2)
                mock_modbus().add_monitor_register.assert_any_call('holding', 2)
                mock_mqtt().publish.assert_any_call('prefix/scale_up_no_value_map', 2, retain=False)
                mock_mqtt().publish.assert_any_call('prefix/scale_down_no_value_map', 1, retain=False)
                mock_mqtt().publish.assert_any_call('prefix/scale_with_value_map', 'b', retain=False)

                msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/scale_up_no_value_map_set', 'utf-8'))
                msg.payload = b'6'
                m._on_message(None, None, msg)
                self.assertEqual(self.modbus_tables['holding'][1], 3)

                msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/scale_down_no_value_map_set', 'utf-8'))
                msg.payload = b'1'
                m._on_message(None, None, msg)
                self.assertEqual(self.modbus_tables['holding'][2], 2)

                msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/scale_with_value_map_set', 'utf-8'))
                msg.payload = b'b'
                m._on_message(None, None, msg)
                self.assertEqual(self.modbus_tables['holding'][3], 3)
                # print(mock_mqtt.mock_calls)
                # print(mock_modbus.mock_calls)

    def test_mask(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register
                mock_modbus().set_value.side_effect = self.write_modbus_register
                self.modbus_tables['holding'][1] = 0xFEF0

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_mask.yaml', MQTT_TOPIC_PREFIX)
                m.connect()
                m.poll()

                mock_mqtt().publish.assert_any_call('prefix/mask_no_scale', 0xFE00, retain=False)
                mock_mqtt().publish.assert_any_call('prefix/no_mask_no_scale', 0xFEF0, retain=False)
                mock_mqtt().publish.assert_any_call('prefix/mask_and_scale', 0xFE, retain=False)

                msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/mask_and_scale_set', 'utf-8'))
                msg.payload = b'255'
                m._on_message(None, None, msg)
                self.assertEqual(self.modbus_tables['holding'][1], 0xFFF0)

                # This register isn't scaled, so 255 won't fill up the MSB.
                msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/mask_no_scale_set', 'utf-8'))
                msg.payload = b'255'
                m._on_message(None, None, msg)
                self.assertEqual(self.modbus_tables['holding'][1], 0x00F0)

                # This should set the LSb of the MSB.
                msg = MQTTMessage(topic=bytes(MQTT_TOPIC_PREFIX+'/mask_no_scale_set', 'utf-8'))
                msg.payload = b'511'
                m._on_message(None, None, msg)
                self.assertEqual(self.modbus_tables['holding'][1], 0x01F0)

    def test_address_offset(self):
        with patch('paho.mqtt.client.Client') as mock_mqtt:
            with patch('modbus4mqtt.modbus_interface.modbus_interface') as mock_modbus:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().get_value.side_effect = self.read_modbus_register

                m = modbus4mqtt.mqtt_interface('kroopit', 1885, 'brengis', 'pranto', './tests/test_address_offset.yaml', MQTT_TOPIC_PREFIX)
                m.connect()

                self.modbus_tables['holding'][0] = 0
                self.modbus_tables['holding'][1] = 1
                self.modbus_tables['holding'][2] = 2
                self.modbus_tables['holding'][3] = 3
                m.poll()

                mock_mqtt().publish.assert_any_call(MQTT_TOPIC_PREFIX+'/publish', 2, retain=False)

if __name__ == "__main__":
    unittest.main()