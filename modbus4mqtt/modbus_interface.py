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
                 write_mode=WriteMode.Multi,
                 variant=None,
                 read_batching=None,
                 write_batching=None,
                 word_order=WordOrder.HighLow
                 ):
        self._ip: str = ip
        self._port: int = port

        self._planned_writes: Queue = Queue()
        self._write_mode: WriteMode = write_mode
        self._unit: int = device_address
        self._variant: str | None = variant
        self._read_batching: int = DEFAULT_READ_BATCHING
        self._write_batching: int = DEFAULT_WRITE_BATCHING
        self._word_order: WordOrder = word_order
        if read_batching is not None:
            if read_batching < MIN_BATCHING:
                logging.warning("Bad value for read_batching: {}. Enforcing minimum value of {}".format(read_batching, MIN_BATCHING))
                self._read_batching = MIN_BATCHING
            elif read_batching > MAX_BATCHING:
                logging.warning("Bad value for read_batching: {}. Enforcing maximum value of {}".format(read_batching, MAX_BATCHING))
                self._read_batching = MAX_BATCHING
            else:
                self._read_batching = read_batching
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
        self._tables: dict[str, ModbusTable] = {
            'input': ModbusTable(self._read_batching, self._write_batching),
            'holding': ModbusTable(self._read_batching, self._write_batching)
        }

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

    def close(self):
        self._mb.close()

    def add_monitor_register(self, table, addr, type='uint16'):
        # Accepts a modbus register and table to monitor
        if table not in self._tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._tables.keys()))
        # Register enough sequential addresses to fill the size of the register type.
        # Note: Each address provides 2 bytes of data.
        for i in range(type_length(type)):
            self._tables[table].add_register(addr+i)

    def poll(self):
        for table in self._tables:
            for start, length in self._tables[table].get_batched_addresses():
                try:
                    values = self._scan_value_range(table, start, length)
                    for offset, value in enumerate(values):
                        self._tables[table].set_value(start + offset, value, write=False)
                except ModbusException as e:
                    if "Failed to connect" in str(e):
                        raise e
                    logging.error(e)
        self._process_writes()

    def get_value(self, table, addr, type='uint16'):
        if table not in self._tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self._tables.keys()))
        if addr not in self._tables[table]:
            raise ValueError("Unpolled address. Use add_monitor_register(addr, table) to add a register to the polled list.")
        # Read sequential addresses to get enough bytes to satisfy the type of this register.
        # Note: Each address provides 2 bytes of data.
        value = bytes(0)
        type_len = type_length(type)
        for i in range(type_len):
            if self._word_order == WordOrder.HighLow:
                data = self._tables[table].get_value(addr + i)
            else:
                data = self._tables[table].get_value(addr + (type_len-i-1))
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
            self._tables['holding'].set_value(addr+i, value, mask, write=True)

        # TODO Determine if we want to do immediate writes here, or leave it to be handled in poll().
        self._process_writes()

    def _perform_write(self, addr, values):
        if self._write_mode == WriteMode.Single or len(values) == 1:
            for i, value in enumerate(values):
                self._mb.write_register(address=addr+i, value=value, device_id=self._unit)
        else:
            self._mb.write_registers(address=addr, values=values, device_id=self._unit)

    def _process_writes(self):
        for start, length in self._tables['holding'].get_batched_addresses(write_mode=True):
            values = []
            for i in range(length):
                values.append(self._tables['holding'].get_value(start + i))
            try:
                self._perform_write(start, values)
            except ModbusException as e:
                logging.error("Failed to write to modbus device: {}".format(e))
        self._tables['holding'].clear_changed_registers()

    def _scan_value_range(self, table, start, count):
        result = None
        if table == 'input':
            result = self._mb.read_input_registers(address=start, count=count, device_id=self._unit)
        elif table == 'holding':
            result = self._mb.read_holding_registers(address=start, count=count, device_id=self._unit)
        if result is None:
            raise ModbusException("No result from modbus read.")
        if len(result.registers) != count:
            raise ModbusException("Expected {} registers from modbus read on {}, got {}.".format(count, start, len(result.registers)))
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
