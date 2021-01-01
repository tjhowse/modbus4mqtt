from time import time, sleep
import logging
from queue import Queue
from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer
from pymodbus import exceptions
from SungrowModbusTcpClient import SungrowModbusTcpClient

DEFAULT_SCAN_RATE_S = 5
DEFAULT_SCAN_BATCHING = 100
MIN_SCAN_BATCHING = 1
MAX_SCAN_BATCHING = 100
DEFAULT_WRITE_BLOCK_INTERVAL_S = 0.2
DEFAULT_WRITE_SLEEP_S = 0.05
DEFAULT_READ_SLEEP_S = 0.05

class modbus_interface():

    def __init__(self, ip, port=502, update_rate_s=DEFAULT_SCAN_RATE_S, variant=None, scan_batching=None):
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

    def add_monitor_register(self, table, addr):
        # Accepts a modbus register and table to monitor
        if table not in self._tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._tables.keys()))
        self._tables[table].add(addr)

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

    def get_value(self, table, addr):
        if table not in self._values:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._values.keys()))
        if addr not in self._values[table]:
            raise ValueError("Unpolled address. Use add_monitor_register(addr, table) to add a register to the polled list.")
        return self._values[table][addr]

    def set_value(self, table, addr, value, mask=0xFFFF):
        if table != 'holding':
            # I'm not sure if this is true for all devices. I might support writing to coils later,
            # so leave this door open.
            raise ValueError("Can only set values in the holding table.")
        self._planned_writes.put((addr, value, mask))
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

def _convert_from_uint16_to_type(value, type):
    type = type.strip().lower()
    if type == 'uint16':
        return value
    elif type == 'int16':
        if value >= 2**15:
            return value - 2**16
        return value
    raise ValueError("Unrecognised type conversion attempted: uint16 to {}".format(type))

def _convert_from_type_to_uint16(value, type):
    type = type.strip().lower()
    if type == 'uint16':
        return value
    elif type == 'int16':
        if value < 0:
            return value + 2**16
        return value
    raise ValueError("Unrecognised type conversion attempted: {} to uint16".format(type))