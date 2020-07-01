from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer

DEFAULT_MODBUS_SCAN_RATE_S = 5
DEFAULT_MODBUS_SCAN_BATCHING = 100

class modbus_interface():

    def __init__(self, ip, port=502, update_rate_s=DEFAULT_MODBUS_SCAN_RATE_S):
        self.ip = ip
        self.port = port
        # This is a dict of sets. Each key represents one table of modbus registers.
        # At the moment it has 'input' and 'holding'
        self.tables = {'input': set(), 'holding': set()}

        # This is a dicts of dicts. These hold the current values of the interesting registers
        self.values = {'input': {}, 'holding': {}}

    def connect(self):
        # Connects to the modbus device
        self.mb = ModbusTcpClient(self.ip, self.port,
                                  framer=ModbusSocketFramer, timeout=1,
                                  RetryOnEmpty=True, retries=1)

    def add_monitor_register(self, table, addr):
        # Accepts a modbus register and table to monitor
        if table not in self.tables:
            raise ValueError("Unsupported table type. Please only use: {}".format(self.tables.keys()))
        self.tables[table].add(addr)

    def poll(self):
        # Polls for the values marked as interesting in self.tables.
        for table in self.tables:
            # This batches up modbus reads in chunks of DEFAULT_MODBUS_SCAN_BATCHING
            start = -1
            for k in sorted(self.tables[table]):
                group = int(k) - int(k) % DEFAULT_MODBUS_SCAN_BATCHING
                if (start < group):
                    values = self.scan_value_range(table, group, DEFAULT_MODBUS_SCAN_BATCHING)
                    for x in range(0, DEFAULT_MODBUS_SCAN_BATCHING):
                        key = group + x + 1
                        self.values[table][key] = values[x]
                    start = group + DEFAULT_MODBUS_SCAN_BATCHING

    def get_value(self, table, addr):
        if table not in self.values:
            raise ValueError("Unsupported table type. Please only use: {}".format(self.values.keys()))
        if addr not in self.values[table]:
            raise ValueError("Unpolled address. Use add_monitor_register(addr, table) to add a register to the polled list.")
        return self.values[table][addr]

    def set_value(self, table, addr, value):
        print("Mock-writing {} to {} in {}".format(value, addr, table))
        if table != 'holding':
            raise ValueError("Can only set values in the holding table.")
        # self.mb.write_register(addr, value, unit=0x01)

    def scan_value_range(self, table, start, count):
        if table == 'input':
            return self.mb.read_input_registers(start, count, unit=0x01).registers
        elif table == 'holding':
            return self.mb.read_holding_registers(start, count, unit=0x01).registers