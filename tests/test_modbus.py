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

    def read_input_registers(self, start, count, unit):
        return self.modbusRegister(registers=self.input_registers.registers[start:start+count])

    def read_holding_registers(self, start, count, unit):
        return self.modbusRegister(registers=self.holding_registers.registers[start:start+count])

    def write_holding_register(self, address, value, unit):
        self.holding_registers.registers[address] = value

    def connect_success(self):
        return False

    def connect_failure(self):
        return True

    def throw_exception(self, addr, value, unit):
        raise ValueError('Oh noooo!')

    def test_connect(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
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

    def test_invalid_tables_and_addresses(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            m = modbus_interface.modbus_interface('1.1.1.1', 111, 2)
            m.connect()

            m.add_monitor_register('holding', 5)
            m.add_monitor_register('input', 6)
            self.assertRaises(ValueError, m.get_value, 'beupe', 5)
            self.assertRaises(ValueError, m.add_monitor_register, 'beupe', 5)
            self.assertRaises(ValueError, m.get_value, 'holding', 1000)

    def test_write_queuing(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            m = modbus_interface.modbus_interface('1.1.1.1', 111, 2)
            m.connect()

            m.add_monitor_register('holding', 5)
            m.add_monitor_register('input', 6)

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

    def test_exception_on_write(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            with self.assertLogs() as mock_logger:
                m = modbus_interface.modbus_interface('1.1.1.1', 111, 2)
                m.connect()

                m.add_monitor_register('holding', 5)
                # Have the write_register throw an exception
                mock_modbus().write_register.side_effect = self.throw_exception
                m.set_value('holding', 5, 7)
                self.assertIn("ERROR:root:Failed to write to modbus device: Oh noooo!", mock_logger.output[-1])

    def test_masked_writes(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            mock_modbus().read_input_registers.side_effect = self.read_input_registers
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers
            mock_modbus().write_register.side_effect = self.write_holding_register

            m = modbus_interface.modbus_interface('1.1.1.1', 111, 2)
            m.connect()

            self.holding_registers.registers[1] = 0
            m.add_monitor_register('holding', 1)

            m.set_value('holding', 1, 0x00FF, 0x00F0)
            self.assertEqual(self.holding_registers.registers[1], 0x00F0)

            m.set_value('holding', 1, 0x00FF, 0x000F)
            self.assertEqual(self.holding_registers.registers[1], 0x00FF)

            m.set_value('holding', 1, 0xFFFF, 0xFF00)
            self.assertEqual(self.holding_registers.registers[1], 0xFFFF)

            m.set_value('holding', 1, 0x0000, 0x0F00)
            self.assertEqual(self.holding_registers.registers[1], 0xF0FF)
