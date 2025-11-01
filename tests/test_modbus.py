import os
from collections import namedtuple
import unittest
from unittest.mock import patch, call, Mock

from modbus4mqtt import modbus_interface
from pymodbus import ModbusException

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
        modbus_interface.DEFAULT_READ_BATCHING = 10
        self.input_registers = self.modbusRegister(registers=list(range(0,modbus_interface.DEFAULT_READ_BATCHING*10)))
        self.holding_registers = self.modbusRegister(registers=list(range(0,modbus_interface.DEFAULT_READ_BATCHING*10)))

    def tearDown(self):
        pass

    def read_input_registers(self, address, count, device_id):
        return self.modbusRegister(registers=self.input_registers.registers[address:address+count])

    def read_holding_registers(self, address, count, device_id):
        return self.modbusRegister(registers=self.holding_registers.registers[address:address+count])

    def write_holding_register(self, address, value, device_id):
        self.holding_registers.registers[address] = value

    def write_holding_registers(self, address, values, device_id):
        for i, value in enumerate(values):
            self.holding_registers.registers[address + i] = value

    def connect_success(self):
        return False

    def connect_failure(self):
        return True

    def throw_exception(self, address, value, device_id):
        raise ModbusException('Oh noooo!')

    def perform_variant_test(self, mock_modbus, variant, expected_framer):
        mock_modbus().connect.side_effect = self.connect_success
        mock_modbus().read_input_registers.side_effect = self.read_input_registers
        mock_modbus().read_holding_registers.side_effect = self.read_holding_registers

        m = modbus_interface.modbus_interface(ip='1.1.1.1', port=111, variant=variant)
        m.connect()
        mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=expected_framer, retries=3, timeout=1)

    def test_connection_variants(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            self.perform_variant_test(mock_modbus, None, modbus_interface.FramerType.SOCKET)
            self.perform_variant_test(mock_modbus, 'tcp', modbus_interface.FramerType.SOCKET)
            self.perform_variant_test(mock_modbus, 'rtu-over-tcp', modbus_interface.FramerType.RTU)
        with patch('modbus4mqtt.modbus_interface.ModbusUdpClient') as mock_modbus:
            self.perform_variant_test(mock_modbus, 'udp', modbus_interface.FramerType.SOCKET)
            self.perform_variant_test(mock_modbus, 'rtu-over-udp', modbus_interface.FramerType.RTU)

        m = modbus_interface.modbus_interface(ip='1.1.1.1', port=111, variant='notexisiting')
        self.assertRaises(ValueError, m.connect)

        m = modbus_interface.modbus_interface(ip='1.1.1.1', port=111, variant='notexisiting-over-tcp')
        self.assertRaises(ValueError, m.connect)

    def test_connect(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            mock_modbus().read_input_registers.side_effect = self.read_input_registers
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers

            m = modbus_interface.modbus_interface(ip='1.1.1.1', port=111, read_batching=10)
            m.connect()
            mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=modbus_interface.FramerType.SOCKET, retries=3, timeout=1)

            # Confirm registers are added to the correct tables.
            m.add_monitor_register('holding', 5)
            m.add_monitor_register('input', 6)
            self.assertIn(5, m._tables['holding'])
            self.assertNotIn(5, m._tables['input'])
            self.assertIn(6, m._tables['input'])
            self.assertNotIn(6, m._tables['holding'])

            m.poll()

            # Just scan a single register, make sure we get a single count=1 read each.
            self.assertEqual(m.get_value('holding', 5), 5)
            self.assertEqual(m.get_value('input', 6), 6)

            mock_modbus().read_holding_registers.assert_any_call(address=5, count=1, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=6, count=1, device_id=1)

            m.set_value('holding', 5, 7)
            m.poll()

            mock_modbus().write_register.assert_any_call(address=5, value=7, device_id=1)

            self.assertRaises(ValueError, m.set_value, 'input', 5, 7)

            mock_modbus().read_holding_registers.reset_mock()
            mock_modbus().read_input_registers.reset_mock()

            # Ensure these registers are polled as a part of a bigger batched read.
            for i in range(6,15):
                m.add_monitor_register('holding', i)
                m.add_monitor_register('input', i+1)

            self.assertIn(14, m._tables['holding'])
            self.assertIn(15, m._tables['input'])

            mock_modbus().read_holding_registers.reset_mock()
            mock_modbus().read_input_registers.reset_mock()

            m.poll()

            mock_modbus().read_holding_registers.assert_any_call(address=5, count=10, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=6, count=10, device_id=1)

            # Add a register beyond the end of the previous batched read to ensure we do another batched read.
            m.add_monitor_register('holding', 15)
            m.add_monitor_register('input', 16)

            self.assertIn(15, m._tables['holding'])
            self.assertIn(16, m._tables['input'])

            mock_modbus().read_holding_registers.reset_mock()
            mock_modbus().read_input_registers.reset_mock()

            m.poll()

            mock_modbus().read_holding_registers.assert_any_call(address=5, count=10, device_id=1)
            mock_modbus().read_holding_registers.assert_any_call(address=15, count=1, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=6, count=10, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=16, count=1, device_id=1)

            # Add a 32b register straddling the boundary of the end of the batch.
            m.add_monitor_register('holding', 16, type='uint32')
            m.add_monitor_register('input', 17, type='uint32')

            self.assertIn(16, m._tables['holding'])
            self.assertIn(17, m._tables['holding'])
            self.assertIn(17, m._tables['input'])
            self.assertIn(18, m._tables['input'])

            mock_modbus().read_holding_registers.reset_mock()
            mock_modbus().read_input_registers.reset_mock()

            m.poll()

            mock_modbus().read_holding_registers.assert_any_call(address=5, count=10, device_id=1)
            mock_modbus().read_holding_registers.assert_any_call(address=15, count=3, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=6, count=10, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=16, count=3, device_id=1)

    def test_invalid_tables_and_addresses(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            m = modbus_interface.modbus_interface('1.1.1.1', 111)
            m.connect()

            m.add_monitor_register('holding', 5)
            m.add_monitor_register('input', 6)
            self.assertRaises(ValueError, m.get_value, 'beupe', 5)
            self.assertRaises(ValueError, m.add_monitor_register, 'beupe', 5)
            self.assertRaises(ValueError, m.get_value, 'holding', 1000)

    def test_exception_on_write(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            with self.assertLogs() as mock_logger:
                m = modbus_interface.modbus_interface('1.1.1.1', 111)
                m.connect()

                m.add_monitor_register('holding', 5)
                # Have the write_register throw an exception
                mock_modbus().write_register.side_effect = self.throw_exception
                m.set_value('holding', 5, 7)
                self.assertIn("ERROR:root:Failed to write to modbus device: Modbus Error: Oh noooo!", mock_logger.output[-1])

    def test_masked_writes(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            mock_modbus().read_input_registers.side_effect = self.read_input_registers
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers
            mock_modbus().write_register.side_effect = self.write_holding_register

            m = modbus_interface.modbus_interface('1.1.1.1', 111)
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

    def test_scan_batching_of_one(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            mock_modbus().read_input_registers.side_effect = self.read_input_registers
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers

            m = modbus_interface.modbus_interface('1.1.1.1', 111, read_batching=1)
            m.connect()
            mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=modbus_interface.FramerType.SOCKET, retries=3, timeout=1)

            # Confirm registers are added to the correct tables.
            m.add_monitor_register('holding', 5)
            m.add_monitor_register('holding', 6)
            m.add_monitor_register('input', 6)
            m.add_monitor_register('input', 7)

            m.poll()

            self.assertEqual(m.get_value('holding', 5), 5)
            self.assertEqual(m.get_value('holding', 6), 6)
            self.assertEqual(m.get_value('input', 6), 6)
            self.assertEqual(m.get_value('input', 7), 7)

            # Ensure each register is scanned with a separate read call.
            mock_modbus().read_holding_registers.assert_any_call(address=5, count=1, device_id=1)
            mock_modbus().read_holding_registers.assert_any_call(address=6, count=1, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=6, count=1, device_id=1)
            mock_modbus().read_input_registers.assert_any_call(address=7, count=1, device_id=1)

    def test_scan_batching_bad_value(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            with self.assertLogs() as mock_logger:
                mock_modbus().connect.side_effect = self.connect_success
                mock_modbus().read_input_registers.side_effect = self.read_input_registers
                mock_modbus().read_holding_registers.side_effect = self.read_holding_registers

                bad_scan_batching = modbus_interface.MAX_BATCHING+1
                modbus_interface.modbus_interface(  '1.1.1.1', 111,
                                                  read_batching=bad_scan_batching,
                                                  write_mode=modbus_interface.WriteMode.Multi)
                self.assertIn("Bad value for scan_batching: {}. Enforcing maximum value of {}".format(bad_scan_batching, modbus_interface.MAX_BATCHING), mock_logger.output[-1])

                bad_scan_batching = modbus_interface.MIN_BATCHING-1
                modbus_interface.modbus_interface(  '1.1.1.1', 111,
                                                  read_batching=bad_scan_batching,
                                                  write_mode=modbus_interface.WriteMode.Multi)
                self.assertIn("Bad value for scan_batching: {}. Enforcing minimum value of {}".format(bad_scan_batching, modbus_interface.MIN_BATCHING), mock_logger.output[-1])

    def test_type_conversions(self):
        a = modbus_interface._convert_from_type_to_bytes(-1, 'int16')
        self.assertEqual(a, b'\xff\xff')
        a = modbus_interface._convert_from_bytes_to_type(a, 'int16')
        self.assertEqual(a, -1)
        a = modbus_interface._convert_from_type_to_bytes(10, 'uint16')
        self.assertEqual(a, b'\x00\x0a')
        a = modbus_interface._convert_from_bytes_to_type(a, 'uint16')
        self.assertEqual(a, 10)

        a = modbus_interface._convert_from_type_to_bytes(-1, 'int32')
        self.assertEqual(a, b'\xff\xff\xff\xff')
        a = modbus_interface._convert_from_bytes_to_type(a, 'int32')
        self.assertEqual(a, -1)
        a = modbus_interface._convert_from_type_to_bytes(689876135, 'uint32')
        self.assertEqual(a, b'\x29\x1E\xAC\xA7')
        a = modbus_interface._convert_from_bytes_to_type(a, 'uint32')
        self.assertEqual(a, 689876135)

        a = modbus_interface._convert_from_type_to_bytes(-1, 'int64')
        self.assertEqual(a, b'\xff\xff\xff\xff\xff\xff\xff\xff')
        a = modbus_interface._convert_from_bytes_to_type(a, 'int64')
        self.assertEqual(a, -1)
        a = modbus_interface._convert_from_type_to_bytes(5464681683516384647, 'uint64')
        self.assertEqual(a, b'\x4B\xD6\x73\x09\xBC\x93\xE5\x87')
        a = modbus_interface._convert_from_bytes_to_type(a, 'uint64')
        self.assertEqual(a, 5464681683516384647)

        try:
            a = modbus_interface._convert_from_bytes_to_type(10, 'float16')
            self.fail("Silently accepted an invalid type conversion.")
        except:
            pass
        try:
            a = modbus_interface._convert_from_type_to_bytes(10, 'float16')
            self.fail("Silently accepted an invalid type conversion.")
        except:
            pass
        try:
            a = modbus_interface.type_length('float16')
            self.fail("Silently accepted an invalid type conversion.")
        except:
            pass

    def test_multi_byte_write_counts(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success

            m = modbus_interface.modbus_interface(  '1.1.1.1', 111,
                                                    word_order=modbus_interface.WordOrder.HighLow,
                                                    write_mode=modbus_interface.WriteMode.Single)

            m.connect()
            mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=modbus_interface.FramerType.SOCKET, retries=3, timeout=1)

            for i in range(1,11):
                m.add_monitor_register('holding', i)

            m.poll()
            # Write a value in.
            m.set_value('holding', 1, 65535, 0xFFFF, 'uint16')
            m.poll()
            # Confirm that it only wrote one register.
            mock_modbus().write_register.assert_any_call(address=1, value=65535, device_id=1)
            mock_modbus().reset_mock()

            m.set_value('holding', 1, 689876135, 0xFFFF, 'uint32')
            m.poll()

            mock_modbus().write_register.assert_any_call(address=1, value=int.from_bytes(b'\x29\x1E','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=2, value=int.from_bytes(b'\xAC\xA7','big'), device_id=1)
            mock_modbus().reset_mock()

            m.set_value('holding', 1, 5464681683516384647, 0xFFFF, 'uint64')
            m.poll()

            mock_modbus().write_register.assert_any_call(address=1, value=int.from_bytes(b'\x4B\xD6','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=2, value=int.from_bytes(b'\x73\x09','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=3, value=int.from_bytes(b'\xBC\x93','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=4, value=int.from_bytes(b'\xE5\x87','big'), device_id=1)
            mock_modbus().reset_mock()

    def test_multi_byte_write_counts_LowHigh_order(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success

            m = modbus_interface.modbus_interface(  '1.1.1.1', 111,
                                                    word_order=modbus_interface.WordOrder.LowHigh,
                                                    write_mode=modbus_interface.WriteMode.Single)
            m.connect()
            mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=modbus_interface.FramerType.SOCKET, retries=3, timeout=1)

            for i in range(1,11):
                m.add_monitor_register('holding', i)
            m.set_value('holding', 1, 689876135, 0xFFFF, 'uint32')
            m.poll()

            mock_modbus().write_register.assert_any_call(address=1, value=int.from_bytes(b'\xAC\xA7','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=2, value=int.from_bytes(b'\x29\x1E','big'), device_id=1)
            mock_modbus().reset_mock()

            m.set_value('holding', 1, 5464681683516384647, 0xFFFF, 'uint64')
            m.poll()

            mock_modbus().write_register.assert_any_call(address=1, value=int.from_bytes(b'\xE5\x87','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=2, value=int.from_bytes(b'\xBC\x93','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=3, value=int.from_bytes(b'\x73\x09','big'), device_id=1)
            mock_modbus().write_register.assert_any_call(address=4, value=int.from_bytes(b'\x4B\xD6','big'), device_id=1)
            mock_modbus().reset_mock()

    def perform_multi_byte_read_write_values_test(self, write_mode):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers
            mock_modbus().write_register.side_effect = self.write_holding_register
            mock_modbus().write_registers.side_effect = self.write_holding_registers

            m = modbus_interface.modbus_interface(  '1.1.1.1', 111,
                                                    read_batching=1,
                                                    write_mode=write_mode)
            m.connect()
            mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=modbus_interface.FramerType.SOCKET, retries=3, timeout=1)

            for i in range(1,11):
                m.add_monitor_register('holding', i)

            m.poll()
            # Write a value in.
            m.set_value('holding', 1, 65535, 0xFFFF, 'uint16')
            m.poll()
            # Read the value out.
            self.assertEqual(m.get_value('holding', 1, 'uint16'), 65535)
            # Read the value out as a different type.
            self.assertEqual(m.get_value('holding', 1, 'int16'), -1)
            m.poll()

            m.set_value('holding', 1, 4294927687, 0xFFFF, 'uint32')
            m.poll()
            # Read the value out.
            self.assertEqual(m.get_value('holding', 1, 'uint32'), 4294927687)
            # Read the value out as a different type.
            self.assertEqual(m.get_value('holding', 1, 'int32'), -39609)

            m.set_value('holding', 1, 18446573203856197441, 0xFFFF, 'uint64')
            m.poll()
            # Read the value out.
            self.assertEqual(m.get_value('holding', 1, 'uint64'), 18446573203856197441)
            # Read the value out as a different type.
            self.assertEqual(m.get_value('holding', 1, 'int64'), -170869853354175)

    def test_multi_byte_read_write_values(self):
        self.perform_multi_byte_read_write_values_test(modbus_interface.WriteMode.Single)
        self.perform_multi_byte_read_write_values_test(modbus_interface.WriteMode.Multi)

    def test_multi_byte_read_write_values_LowHigh(self):
        with patch('modbus4mqtt.modbus_interface.ModbusTcpClient') as mock_modbus:
            mock_modbus().connect.side_effect = self.connect_success
            mock_modbus().read_holding_registers.side_effect = self.read_holding_registers
            mock_modbus().write_register.side_effect = self.write_holding_register

            m = modbus_interface.modbus_interface(  '1.1.1.1', 111,
                                                    read_batching=1,
                                                    word_order=modbus_interface.WordOrder.LowHigh,
                                                    write_mode=modbus_interface.WriteMode.Single
                                                    )
            m.connect()
            mock_modbus.assert_called_with(host='1.1.1.1', port=111, framer=modbus_interface.FramerType.SOCKET, retries=3, timeout=1)

            for i in range(1,11):
                m.add_monitor_register('holding', i)

            m.poll()
            # Write a value in.
            m.set_value('holding', 1, 65535, 0xFFFF, 'uint16')
            m.poll()
            # Read the value out.
            self.assertEqual(m.get_value('holding', 1, 'uint16'), 65535)
            # Read the value out as a different type.
            self.assertEqual(m.get_value('holding', 1, 'int16'), -1)
            m.poll()

            m.set_value('holding', 1, 4294927687, 0xFFFF, 'uint32')
            m.poll()
            # Read the value out.
            self.assertEqual(m.get_value('holding', 1, 'uint32'), 4294927687)
            # Read the value out as a different type.
            self.assertEqual(m.get_value('holding', 1, 'int32'), -39609)

            m.set_value('holding', 1, 18446573203856197441, 0xFFFF, 'uint64')
            m.poll()
            # Read the value out.
            self.assertEqual(m.get_value('holding', 1, 'uint64'), 18446573203856197441)
            # Read the value out as a different type.
            self.assertEqual(m.get_value('holding', 1, 'int64'), -170869853354175)
