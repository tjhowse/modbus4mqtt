from time import time, sleep, monotonic

from enum import Enum
import logging
from queue import Queue
from pymodbus.client import ModbusTcpClient, ModbusUdpClient, ModbusTlsClient
from pymodbus.framer import FramerType
from pymodbus import ModbusException

from SungrowModbusTcpClient import SungrowModbusTcpClient
from modbus4mqtt.modbus_table import ModbusTable

DEFAULT_READ_BATCHING = 100
DEFAULT_WRITE_BATCHING = 100
MIN_BATCHING = 1
MAX_BATCHING = 100
DEFAULT_WRITE_BLOCK_INTERVAL_S = 0.2
DEFAULT_WRITE_SLEEP_S = 0.05
DEFAULT_READ_SLEEP_S = 0.05


class WordOrder(Enum):
    HighLow = 1
    LowHigh = 2


class WriteMode(Enum):
    Single = 1
    Multi = 2


class modbus_interface():

    def __init__(self,
                 ip,
                 port=502,
                 device_address=0x01,
                 write_mode=WriteMode.Single,
                 variant=None,
                 scan_batching=None,
                 write_batching=None,
                 word_order=WordOrder.HighLow
                 ):
        self._ip: str = ip
        self._port: int = port
        # This is a dict of sets. Each key represents one table of modbus registers.
        # At the moment it has 'input' and 'holding'
        self._tables: dict[str, set[int]] = {'input': set(), 'holding': set()}

        # This is a dicts of dicts. These hold the current values of the interesting registers
        self._values: dict[str, dict[int, int]] = {'input': {}, 'holding': {}}

        self._planned_writes: Queue = Queue()
        self._writing: bool = False
        self._write_mode: WriteMode = write_mode
        self._unit: int = device_address
        self._variant: str | None = variant
        self._read_batching: int = DEFAULT_READ_BATCHING
        self._write_batching: int = DEFAULT_WRITE_BATCHING
        self._word_order: WordOrder = word_order
        if scan_batching is not None:
            if scan_batching < MIN_BATCHING:
                logging.warning("Bad value for scan_batching: {}. Enforcing minimum value of {}".format(scan_batching, MIN_BATCHING))
                self._read_batching = MIN_BATCHING
            elif scan_batching > MAX_BATCHING:
                logging.warning("Bad value for scan_batching: {}. Enforcing maximum value of {}".format(scan_batching, MAX_BATCHING))
                self._read_batching = MAX_BATCHING
            else:
                self._read_batching = scan_batching
        if write_batching is not None:
            if write_batching < MIN_BATCHING:
                logging.warning("Bad value for write_batching: {}. Enforcing minimum value of {}".format(write_batching, MIN_BATCHING))
                self._write_batching = MIN_BATCHING
            elif write_batching > MAX_BATCHING:
                logging.warning("Bad value for write_batching: {}. Enforcing maximum value of {}".format(write_batching, MAX_BATCHING))
                self._write_batching = MAX_BATCHING
            else:
                self._write_batching = write_batching
        if self._write_mode == WriteMode.Single:
            logging.warning("Overriding write batching to 1 due to single write mode.")
            self._write_batching = 1
        self._tables_new: dict[str, ModbusTable] = {'input': ModbusTable(self._read_batching), 'holding': ModbusTable(self._read_batching)}

    def connect(self) -> bool:
        # Connects to the modbus device. Returns True on success, False on failure.
        clients = {
            "tcp": ModbusTcpClient,
            "tls": ModbusTlsClient,
            "udp": ModbusUdpClient,
            "sungrow": SungrowModbusTcpClient.SungrowModbusTcpClient,
            # if 'serial' modbus is required at some point, the configuration
            # needs to be changed to provide file, baudrate etc.
            # "serial": (ModbusSerialClient, ModbusRtuFramer),
        }
        framers = {
            "ascii": FramerType.ASCII,
            "rtu": FramerType.RTU,
            "socket": FramerType.SOCKET,
            "tls": FramerType.TLS,
        }

        if self._variant is None:
            desired_framer, desired_client = None, 'tcp'
        elif "-over-" in self._variant:
            desired_framer, desired_client = self._variant.split('-over-')
        else:
            desired_framer, desired_client = None, self._variant

        if desired_client not in clients:
            raise ValueError("Unknown modbus client: {}".format(desired_client))
        if desired_framer is not None and desired_framer not in framers:
            raise ValueError("Unknown modbus framer: {}".format(desired_framer))

        client = clients[desired_client]

        if desired_framer is None:
            desired_framer = "socket"
        framer = framers[desired_framer]

        self._mb = client(host=self._ip, port=self._port, framer=framer, retries=3, timeout=1)
        self._mb.connect()
        return self._mb.connected

    def add_monitor_register(self, table, addr, type='uint16'):
        # Accepts a modbus register and table to monitor
        if table not in self._tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._tables.keys()))
        # Register enough sequential addresses to fill the size of the register type.
        # Note: Each address provides 2 bytes of data.
        for i in range(type_length(type)):
            self._tables[table].add(addr+i)
            self._tables_new[table].add_register(addr+i)

    def poll_new(self):
        for table in self._tables:
            for batch in self._tables_new[table].get_batched_addresses():
                for start, length in batch:
                    try:
                        values = self._scan_value_range(table, start, length)
                        for value in values:
                            self._tables_new[table].set_value(start, value)
                            start += 1
                    except ModbusException as e:
                        if "Failed to connect" in str(e):
                            raise e
                        logging.error(e)
        self.process_writes_new()

    def poll(self):
        self.poll_old()

    def poll_old(self):
        # Polls for the values marked as interesting in self._tables.
        for table in self._tables:
            if len(self._tables[table]) == 0:
                continue
            # This batches up modbus reads in chunks of self._scan_batching
            last_address = max(self._tables[table])
            end_of_previous_read_range = -1
            for k in sorted(self._tables[table]):
                if int(k) <= end_of_previous_read_range:
                    # We've already read this address in a previous batch read.
                    continue
                batch_start = int(k)
                # Ensure we don't read past the last interesting address, in case it's on a boundary.
                batch_size = min(self._read_batching, last_address - batch_start + 1)
                try:
                    values = self._scan_value_range(table, batch_start, batch_size)
                    for x in range(0, batch_size):
                        key = batch_start + x
                        self._values[table][key] = values[x]
                    # Avoid back-to-back read operations that could overwhelm some modbus devices.
                    sleep(DEFAULT_READ_SLEEP_S)
                except ModbusException as e:
                    if "Failed to connect" in str(e):
                        raise e
                    logging.error(e)
                end_of_previous_read_range = batch_start + batch_size-1
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
            value += data.to_bytes(2, 'big')
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

    def _perform_write(self, addr, values):
        if self._write_mode == WriteMode.Single:
            for value in values:
                self._mb.write_register(address=addr, value=value, device_id=self._unit)
        else:
            self._mb.write_registers(address=addr, values=values, device_id=self._unit)

    def _process_writes(self, max_block_s=DEFAULT_WRITE_BLOCK_INTERVAL_S):
        # TODO I am not entirely happy with this system. It's supposed to prevent
        # anything overwhelming the modbus interface with a heap of rapid writes,
        # but without its own event loop it could be quite a while between calls to
        # .poll()...
        if self._writing:
            return
        write_start_time = monotonic()
        self._writing = True
        try:
            while not self._planned_writes.empty() and (monotonic() - write_start_time) < max_block_s:
                addr, value, mask = self._planned_writes.get()
                try:
                    if mask == 0xFFFF:
                        self._perform_write(addr, [value])
                    else:
                        # https://pymodbus.readthedocs.io/en/latest/source/library/pymodbus.client.html?highlight=mask_write_register#pymodbus.client.common.ModbusClientMixin.mask_write_register
                        # https://www.mathworks.com/help/instrument/modify-the-contents-of-a-holding-register-using-a-mask-write.html
                        # Result = (register value AND andMask) OR (orMask AND (NOT andMask))
                        # This bit-shift weirdness is to avoid a mask of 0x0001 resulting in a ~mask of -2, which pymodbus doesn't like.
                        # This means the result will be 65534, AKA 0xFFFE.
                        # This specific read-before-write operation doesn't work on my modbus solar inverter -
                        # I get "Modbus Error: [Input/Output] Modbus Error: [Invalid Message] Incomplete message received, expected at least 8 bytes (0 received)"
                        # I suspect it's a different modbus opcode that tries to do clever things that my device doesn't support.
                        # result = self._mb.mask_write_register(address=addr, and_mask=(1<<16)-1-mask, or_mask=value, device_id=0x01)
                        # print("Result: {}".format(result))
                        old_value = self._scan_value_range('holding', addr, 1)[0]
                        and_mask = (1 << 16) - 1 - mask
                        or_mask = value
                        new_value = (old_value & and_mask) | (or_mask & (mask))
                        self._perform_write(addr, [new_value])
                except ModbusException as e:
                    logging.error("Failed to write to modbus device: {}".format(e))
                sleep(DEFAULT_WRITE_SLEEP_S)
        finally:
            self._writing = False

    def process_writes_new(self):
        for addr, value, mask in self._planned_writes:
            self._tables_new['holding'].set_value(addr, value, mask)
        for start, length in self._tables_new['holding'].get_batched_addresses(write_mode=True):
            values = []
            for i in range(length):
                values.append(self._tables_new['holding'].get_value(start + i))
            self._perform_write(start, values)

    def _scan_value_range(self, table, start, count):
        result = None
        if table == 'input':
            result = self._mb.read_input_registers(address=start, count=count, device_id=self._unit)
        elif table == 'holding':
            result = self._mb.read_holding_registers(address=start, count=count, device_id=self._unit)
        if result is None:
            raise ModbusException("No result from modbus read.")
        if len(result.registers) != count:
            raise ModbusException("Expected {} registers from modbus read, got {}.".format(count, len(result.registers)))
        return result.registers


def type_length(type):
    # Return the number of addresses needed for the type.
    # Note: Each address provides 2 bytes of data.
    if type in ['int16', 'uint16']:
        return 1
    elif type in ['int32', 'uint32']:
        return 2
    elif type in ['int64', 'uint64']:
        return 4
    raise ValueError("Unsupported type {}".format(type))


def type_signed(type):
    # Returns whether the provided type is signed
    if type in ['uint16', 'uint32', 'uint64']:
        return False
    elif type in ['int16', 'int32', 'int64']:
        return True
    raise ValueError("Unsupported type {}".format(type))


def _convert_from_bytes_to_type(value, type):
    type = type.strip().lower()
    signed = type_signed(type)
    return int.from_bytes(value, byteorder='big', signed=signed)


def _convert_from_type_to_bytes(value, type):
    type = type.strip().lower()
    signed = type_signed(type)
    # This can throw an OverflowError in various conditons. This will usually
    # percolate upwards and spit out an exception from on_message.
    return int(value).to_bytes(type_length(type) * 2, byteorder='big', signed=signed)
