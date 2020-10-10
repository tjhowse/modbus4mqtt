from time import time, sleep
import logging
from queue import Queue
from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer
from pymodbus import exceptions
from SungrowModbusTcpClient import SungrowModbusTcpClient

DEFAULT_SCAN_RATE_S = 5
DEFAULT_SCAN_BATCHING = 100
DEFAULT_WRITE_BLOCK_INTERVAL_S = 0.2
DEFAULT_WRITE_SLEEP_S = 0.05

class modbus_interface():

    def __init__(self, ip, port=502, update_rate_s=DEFAULT_SCAN_RATE_S, variant=None):
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

    def connect(self):
        # Connects to the modbus device
        if self._variant == 'sungrow':
            # Some later versions of the sungrow inverter firmware encrypts the payloads of
            # the modbus traffic. https://github.com/rpvelloso/Sungrow-Modbus is a drop-in
            # replacement for ModbusTcpClient that manages decrypting the traffic for us.
            self._mb = SungrowModbusTcpClient(self._ip, self._port,
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
            # This batches up modbus reads in chunks of DEFAULT_SCAN_BATCHING
            start = -1
            for k in sorted(self._tables[table]):
                group = int(k) - int(k) % DEFAULT_SCAN_BATCHING
                if (start < group):
                    try:
                        values = self._scan_value_range(table, group, DEFAULT_SCAN_BATCHING)
                        for x in range(0, DEFAULT_SCAN_BATCHING):
                            key = group + x
                            self._values[table][key] = values[x]
                    except ValueError as e:
                        logging.exception("{}".format(e))
                    start = group + DEFAULT_SCAN_BATCHING-1
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
        if isinstance(result, exceptions.ModbusIOException):
            raise ValueError("Failed to read {} table registers from {} to {}".format(table, start, start+count))
        return result.registers