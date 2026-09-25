class ModbusTable:

    def __init__(self, read_batch_size: int = 100, write_batch_size: int = 0):
        self._registers: dict[int, int] = {}
        # Subset of self._registers that we actually poll. Write-only
        # addresses (e.g. CG-style controllers with disjoint read/write
        # address spaces) land in self._registers via set_value but must
        # NOT be polled, or the read will time out and poison the client.
        self._monitored: set[int] = set()
        # This flag is cleared when the register list is sorted
        # and the batching is calculated.
        self._batches: list[tuple[int, int]] = []
        self._stale: bool = True
        self._read_batch_size = read_batch_size
        if write_batch_size > 0:
            self._write_batch_size = write_batch_size
        else:
            self._write_batch_size = read_batch_size
        # These values have changed since the last write operation
        # and should be included in the next one.
        self._changed_registers: set[int] = set()

    def add_register(self, addr: int):
        self._registers[addr] = 0
        self._monitored.add(addr)
        self._stale = True

    def sort(self):
        # This sorts the registers by address.
        self._registers = dict(sorted(self._registers.items()))

    def get_batched_addresses(self, write_mode: bool = False) -> list[tuple[int, int]]:
        if write_mode:
            # Write batches are not cached: they're cheap to recompute and
            # always reflect _changed_registers only. The cache is reserved
            # for the read scan so we don't accidentally poison polled
            # addresses with write-only targets that arrive before the
            # first poll (e.g. retained MQTT /set messages at startup).
            if not self._changed_registers:
                return []
            return self._generate_batched_addresses(write_mode=True)
        if self._stale:
            self.sort()
            self._stale = False
            self._batches = self._generate_batched_addresses(write_mode=False)
        return self._batches

    def _generate_batched_addresses(
        self, write_mode: bool = False
    ) -> list[tuple[int, int]]:
        # This returns a list of pair tuples. Each tuple is the start and length
        # of a range of addresses that can be read/written together.
        # If "write_mode" is true, the returned lists will only include
        # registers that've changed since the last read operation.
        result: list[tuple[int, int]] = []
        current_batch_start: int = -1
        current_batch_size: int = 0
        previous_addr = None
        if write_mode:
            max_batch_size = self._write_batch_size
        else:
            max_batch_size = self._read_batch_size
        for addr in self._registers:
            if write_mode:
                if addr not in self._changed_registers:
                    continue
            else:
                if addr not in self._monitored:
                    continue
            if current_batch_size >= max_batch_size or (
                previous_addr is not None and addr != previous_addr + 1
            ):
                result.append((current_batch_start, current_batch_size))
                current_batch_start = addr
                current_batch_size = 1
            else:
                if current_batch_start == -1:
                    current_batch_start = addr
                current_batch_size += 1
            previous_addr = addr
        # Don't forget to add the last batch
        if current_batch_start != -1:
            result.append((current_batch_start, current_batch_size))
        return result

    def clear_changed_registers(self):
        self._changed_registers = set()

    def set_value(self, addr: int, value: int, mask: int = 0xFFFF, write: bool = False):
        if addr not in self._registers:
            if not write:
                raise ValueError("Address {} not in monitored registers.".format(addr))
            # CG-style PLCs use disjoint address spaces for reads and writes,
            # so write targets will not appear in the polled register table.
            # Track the address on first write so the batching/write pipeline
            # downstream can flush it.
            self._registers[addr] = 0
            self._stale = True
        if value < 0 or value > 0xFFFF:
            raise ValueError("Value {} out of range for modbus register.".format(value))
        new_value = self._registers[addr] & (~mask) | (value & mask)
        if write:
            # A polled register's cache is the device's last observed value, so
            # an equal write is redundant. An unpolled register's cache is only
            # our last write, which may be stale or a command register that
            # acts on every write, so always send it.
            if new_value != self._registers[addr] or addr not in self._monitored:
                self._changed_registers.add(addr)
        self._registers[addr] = new_value

    def get_value(self, addr: int) -> int:
        if addr not in self._registers:
            raise ValueError("Address {} not in monitored registers.".format(addr))
        return self._registers[addr]

    def __contains__(self, addr: int) -> bool:
        return addr in self._registers

    def __len__(self) -> int:
        return len(self._registers)

    def __getitem__(self, addr: int) -> int:
        return self.get_value(addr)

    def __setitem__(self, addr: int, value: int):
        self.set_value(addr, value, write=False)
