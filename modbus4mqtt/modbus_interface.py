from time import time, sleep
import logging
from queue import Queue
from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer

DEFAULT_SCAN_RATE_S = 5
DEFAULT_SCAN_BATCHING = 100
DEFAULT_WRITE_BLOCK_INTERVAL_S = 0.2
DEFAULT_WRITE_SLEEP_S = 0.05

class modbus_interface():

    def __init__(self, ip, port=502, update_rate_s=DEFAULT_SCAN_RATE_S):
        self._ip = ip
        self._port = port
        # This is a dict of sets. Each key represents one table of modbus registers.
        # At the moment it has 'input' and 'holding'
        self._tables = {'input': set(), 'holding': set()}

        # This is a dicts of dicts. These hold the current values of the interesting registers
        self._values = {'input': {}, 'holding': {}}

        self._planned_writes = Queue()
        self._writing = False

    def connect(self):
        # Connects to the modbus device
        self._mb = ModbusTcpClient(self._ip, self._port,
                                  framer=ModbusSocketFramer, timeout=1,
                                  RetryOnEmpty=True, retries=1)

    def add_monitor_register(self, table, addr):
        # Accepts a modbus register and table to monitor
        if table not in self._tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._tables.keys()))
        self._tables[table].add(addr)

    def poll(self):
        self._process_writes()
        # Polls for the values marked as interesting in self._tables.
        for table in self._tables:
            # This batches up modbus reads in chunks of DEFAULT_SCAN_BATCHING
            start = -1
            for k in sorted(self._tables[table]):
                group = int(k) - int(k) % DEFAULT_SCAN_BATCHING
                if (start < group):
                    values = self._scan_value_range(table, group, DEFAULT_SCAN_BATCHING)
                    for x in range(0, DEFAULT_SCAN_BATCHING):
                        key = group + x
                        self._values[table][key] = values[x]
                    start = group + DEFAULT_SCAN_BATCHING-1

    def get_value(self, table, addr):
        if table not in self._values:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._values.keys()))
        if addr not in self._values[table]:
            raise ValueError("Unpolled address. Use add_monitor_register(addr, table) to add a register to the polled list.")
        return self._values[table][addr]

    def set_value(self, table, addr, value):
        if table != 'holding':
            # I'm not sure if this is true for all devices. I might support writing to coils later,
            # so leave this door open.
            raise ValueError("Can only set values in the holding table.")
        self._planned_writes.put((addr, value))
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
                addr, value = self._planned_writes.get()
                self._mb.write_register(addr, value, unit=0x01)
                sleep(DEFAULT_WRITE_SLEEP_S)
        except:
            logging.error("Failed to write to modbus device.")
        finally:
            self._writing = False

    def _scan_value_range(self, table, start, count):
        if table == 'input':
            return self._mb.read_input_registers(start, count, unit=0x01).registers
        elif table == 'holding':
            return self._mb.read_holding_registers(start, count, unit=0x01).registers