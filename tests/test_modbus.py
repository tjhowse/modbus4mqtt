import os
from collections import namedtuple
import unittest
from unittest.mock import patch, call, Mock
from paho.mqtt.client import MQTTMessage

from modbus4mqtt import modbus_interface

def assert_no_call(self, *args, **kwargs):
    try:
        self.assert_any_call(*args, **kwargs)
    except AssertionError:
        return
    raise AssertionError('Expected %s to not have been called.' % self._format_mock_call_signature(args, kwargs))

Mock.assert_no_call = assert_no_call

MQTT_TOPIC_PREFIX = 'prefix'

class ModbusTests(unittest.TestCase):
    modbusRegister = namedtuple('modbusRegister', 'registers')

    def setUp(self):
        modbus_interface.DEFAULT_SCAN_BATCHING = 10
        self.input_registers = self.modbusRegister(registers=list(range(0,modbus_interface.DEFAULT_SCAN_BATCHING*2)))
        self.holding_registers = self.modbusRegister(registers=list(range(0,modbus_interface.DEFAULT_SCAN_BATCHING*2)))

    def tearDown(self):
        pass

    def read_input_registers(self, star, count, unit):
        return self.input_registers

    def read_holding_registers(self, star, count, unit):
        return self.holding_registers

    def test_connect(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().read_input_registers.side_effect = self.read_input_registers
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers

            m = modbus_interface.modbus_interface('1.1.1.1', 111, 2)
            m.connect()
            mock_modbus.assert_called_with('1.1.1.1', 111, RetryOnEmpty=True, framer=modbus_interface.ModbusSocketFramer, retries=1, timeout=1)

            # Confirm registers are added to the correct tables.
            m.add_monitor_register('holding', 5)
            m.add_monitor_register('input', 6)
            self.assertIn(5, m._tables['holding'])
            self.assertNotIn(5, m._tables['input'])
            self.assertIn(6, m._tables['input'])
            self.assertNotIn(6, m._tables['holding'])

            m.poll()

            self.assertEqual(m.get_value('holding', 5), 5)
            self.assertEqual(m.get_value('input', 6), 6)

            # Ensure we read a batch of DEFAULT_SCAN_BATCHING registers even though we only
            # added one register in each table as interesting
            mock_modbus().read_holding_registers.assert_any_call(0, 10, unit=1)
            mock_modbus().read_input_registers.assert_any_call(0, 10, unit=1)

            m.set_value('holding', 5, 7)
            m.poll()
            mock_modbus().write_register.assert_any_call(5, 7, unit=1)

            self.assertRaises(ValueError, m.set_value, 'input', 5, 7)

            mock_modbus().read_holding_registers.reset_mock()
            mock_modbus().read_input_registers.reset_mock()

            # Ensure this causes two batched reads per table, one from 0-9 and one from 10-19.
            m.add_monitor_register('holding', 15)
            m.add_monitor_register('input', 16)

            self.assertIn(15, m._tables['holding'])
            self.assertIn(16, m._tables['input'])

            m.poll()

            mock_modbus().read_holding_registers.assert_any_call(0, 10, unit=1)
            mock_modbus().read_holding_registers.assert_any_call(10, 10, unit=1)
            mock_modbus().read_input_registers.assert_any_call(0, 10, unit=1)
            mock_modbus().read_input_registers.assert_any_call(10, 10, unit=1)

            self.assertRaises(ValueError, m.get_value, 'beupe', 5)
            self.assertRaises(ValueError, m.add_monitor_register, 'beupe', 5)
            self.assertRaises(ValueError, m.get_value, 'holding', 1000)

            # Check that the write queuing works properly.
            mock_modbus().write_register.reset_mock()
            m._writing = True
            m.set_value('holding', 5, 7)
            m.poll()
            mock_modbus().write_register.assert_not_called()
            m._writing = False
            m.set_value('holding', 6, 8)
            mock_modbus().write_register.assert_any_call(5, 7, unit=1)
            mock_modbus().write_register.assert_any_call(6, 8, unit=1)
