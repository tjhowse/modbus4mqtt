from time import time, sleep
from enum import Enum
import logging
from queue import Queue
try:
    # Pymodbus >= 3.0
    # TODO: Once SungrowModbusTcpClient 0.1.7 is released,
    # we can remove the "<3.0.0" pymodbus restriction and this
    # will make sense again.
    from pymodbus.client import ModbusTcpClient
    from pymodbus.transaction import ModbusSocketFramer
except ImportError:
    # Pymodbus < 3.0
    from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer
from SungrowModbusTcpClient import SungrowModbusTcpClient

DEFAULT_SCAN_RATE_S = 5
DEFAULT_SCAN_BATCHING = 100
MIN_SCAN_BATCHING = 1
MAX_SCAN_BATCHING = 100
DEFAULT_WRITE_BLOCK_INTERVAL_S = 0.2
DEFAULT_WRITE_SLEEP_S = 0.05
DEFAULT_READ_SLEEP_S = 0.05

class WordOrder(Enum):
    HighLow = 1
    LowHigh = 2

class modbus_interface():

    def __init__(self, ip, port=502, update_rate_s=DEFAULT_SCAN_RATE_S, variant=None, scan_batching=None, word_order=WordOrder.HighLow):
        self._ip = ip
        self._port = port
        # This is a dict of sets. Each key represents one table of modbus registers.
        # At the moment it has 'input' and 'holding'
        self._tables = {'input': set(), 'holding': set()}

        # This is a dicts of dicts. These hold the current values of the interesting registers
        self._values = {'input': {}, 'holding': {}}

        self._planned_writes = Queue()
        self._writing = False
        self._variant = variant
        self._scan_batching = DEFAULT_SCAN_BATCHING
        self._word_order = word_order
        if scan_batching is not None:
            if scan_batching < MIN_SCAN_BATCHING:
                logging.warning("Bad value for scan_batching: {}. Enforcing minimum value of {}".format(scan_batching, MIN_SCAN_BATCHING))
                self._scan_batching = MIN_SCAN_BATCHING
            elif scan_batching > MAX_SCAN_BATCHING:
                logging.warning("Bad value for scan_batching: {}. Enforcing maximum value of {}".format(scan_batching, MAX_SCAN_BATCHING))
                self._scan_batching = MAX_SCAN_BATCHING
            else:
                self._scan_batching = scan_batching

    def connect(self):
        # Connects to the modbus device
        if self._variant == 'sungrow':
            # Some later versions of the sungrow inverter firmware encrypts the payloads of
            # the modbus traffic. https://github.com/rpvelloso/Sungrow-Modbus is a drop-in
            # replacement for ModbusTcpClient that manages decrypting the traffic for us.
            self._mb = SungrowModbusTcpClient.SungrowModbusTcpClient(host=self._ip, port=self._port,
                                              framer=ModbusSocketFramer, timeout=1,
                                              RetryOnEmpty=True, retries=1)
        else:
            self._mb = ModbusTcpClient(self._ip, self._port,
                                       framer=ModbusSocketFramer, timeout=1,
                                       RetryOnEmpty=True, retries=1)

    def add_monitor_register(self, table, addr, type='uint16'):
        # Accepts a modbus register and table to monitor
        if table not in self._tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._tables.keys()))
        # Register enough sequential addresses to fill the size of the register type.
        # Note: Each address provides 2 bytes of data.
        for i in range(type_length(type)):
            self._tables[table].add(addr+i)

    def poll(self):
        # Polls for the values marked as interesting in self._tables.
        for table in self._tables:
            # This batches up modbus reads in chunks of self._scan_batching
            start = -1
            for k in sorted(self._tables[table]):
                group = int(k) - int(k) % self._scan_batching
                if (start < group):
                    try:
                        values = self._scan_value_range(table, group, self._scan_batching)
                        for x in range(0, self._scan_batching):
                            key = group + x
                            self._values[table][key] = values[x]
                        # Avoid back-to-back read operations that could overwhelm some modbus devices.
                        sleep(DEFAULT_READ_SLEEP_S)
                    except ValueError as e:
                        logging.exception("{}".format(e))
                    start = group + self._scan_batching-1
        self._process_writes()

    def get_value(self, table, addr, type='uint16'):
        if table not in self._values:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._values.keys()))
        if addr not in self._values[table]:
            raise ValueError("Unpolled address. Use add_monitor_register(addr, table) to add a register to the polled list.")
        # Read sequential addresses to get enough bytes to satisfy the type of this register.
        # Note: Each address provides 2 bytes of data.
        value = bytes(0)
        type_len = type_length(type)
        for i in range(type_len):
            if self._word_order == WordOrder.HighLow:
                data = self._values[table][addr + i]
            else:
                data = self._values[table][addr + (type_len-i-1)]
            value += data.to_bytes(2,'big')
        value = _convert_from_bytes_to_type(value, type)
        return value

    def set_value(self, table, addr, value, mask=0xFFFF, type='uint16'):
        if table != 'holding':
            # I'm not sure if this is true for all devices. I might support writing to coils later,
            # so leave this door open.
            raise ValueError("Can only set values in the holding table.")

        bytes_to_write = _convert_from_type_to_bytes(value, type)
        # Put the bytes into _planned_writes stitched into two-byte pairs

        type_len = type_length(type)
        for i in range(type_len):
            if self._word_order == WordOrder.HighLow:
                value = _convert_from_bytes_to_type(bytes_to_write[i*2:i*2+2], 'uint16')
            else:
                value = _convert_from_bytes_to_type(bytes_to_write[(type_len-i-1)*2:(type_len-i-1)*2+2], 'uint16')
            self._planned_writes.put((addr+i, value, mask))

        self._process_writes()

    def _process_writes(self, max_block_s=DEFAULT_WRITE_BLOCK_INTERVAL_S):
        # TODO I am not entirely happy with this system. It's supposed to prevent
        # anything overwhelming the modbus interface with a heap of rapid writes,
        # but without its own event loop it could be quite a while between calls to
        # .poll()...
        if self._writing:
            return
        write_start_time = time()
        try:
            self._writing = True
            while not self._planned_writes.empty() and (time() - write_start_time) < max_block_s:
                addr, value, mask = self._planned_writes.get()
                if mask == 0xFFFF:
                    self._mb.write_register(addr, value, unit=0x01)
                else:
                    # https://pymodbus.readthedocs.io/en/latest/source/library/pymodbus.client.html?highlight=mask_write_register#pymodbus.client.common.ModbusClientMixin.mask_write_register
                    # https://www.mathworks.com/help/instrument/modify-the-contents-of-a-holding-register-using-a-mask-write.html
                    # Result = (register value AND andMask) OR (orMask AND (NOT andMask))
                    # This bit-shift weirdness is to avoid a mask of 0x0001 resulting in a ~mask of -2, which pymodbus doesn't like.
                    # This means the result will be 65534, AKA 0xFFFE.
                    # This specific read-before-write operation doesn't work on my modbus solar inverter -
                    # I get "Modbus Error: [Input/Output] Modbus Error: [Invalid Message] Incomplete message received, expected at least 8 bytes (0 received)"
                    # I suspect it's a different modbus opcode that tries to do clever things that my device doesn't support.
                    # result = self._mb.mask_write_register(address=addr, and_mask=(1<<16)-1-mask, or_mask=value, unit=0x01)
                    # print("Result: {}".format(result))
                    old_value = self._scan_value_range('holding', addr, 1)[0]
                    and_mask = (1<<16)-1-mask
                    or_mask = value
                    new_value = (old_value & and_mask) | (or_mask & (mask))
                    self._mb.write_register(addr, new_value, unit=0x01)
                sleep(DEFAULT_WRITE_SLEEP_S)
        except Exception as e:
            # BUG catch only the specific exception that means pymodbus failed to write to a register
            # the modbus device doesn't support, not an error at the TCP layer.
            logging.exception("Failed to write to modbus device: {}".format(e))
        finally:
            self._writing = False

    def _scan_value_range(self, table, start, count):
        result = None
        if table == 'input':
            result = self._mb.read_input_registers(start, count, unit=0x01)
        elif table == 'holding':
            result = self._mb.read_holding_registers(start, count, unit=0x01)
        try:
            return result.registers
        except:
            # The result doesn't have a registers attribute, something has gone wrong!
            raise ValueError("Failed to read {} {} table registers starting from {}: {}".format(count, table, start, result))

def type_length(type):
    # Return the number of addresses needed for the type.
    # Note: Each address provides 2 bytes of data.
    if type in ['int16', 'uint16']:
        return 1
    elif type in ['int32', 'uint32']:
        return 2
    elif type in ['int64', 'uint64']:
        return 4
    raise ValueError ("Unsupported type {}".format(type))

def type_signed(type):
    # Returns whether the provided type is signed
    if type in ['uint16', 'uint32', 'uint64']:
        return False
    elif type in ['int16', 'int32', 'int64']:
        return True
    raise ValueError ("Unsupported type {}".format(type))

def _convert_from_bytes_to_type(value, type):
    type = type.strip().lower()
    signed = type_signed(type)
    return int.from_bytes(value,byteorder='big',signed=signed)

def _convert_from_type_to_bytes(value, type):
    type = type.strip().lower()
    signed = type_signed(type)
    # This can throw an OverflowError in various conditons. This will usually
    # percolate upwards and spit out an exception from on_message.
    return int(value).to_bytes(type_length(type)*2,byteorder='big',signed=signed)
