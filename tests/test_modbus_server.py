from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusSparseDataBlock
import threading
import asyncio
import time

from pymodbus import ModbusDeviceIdentification
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer
import asyncio

class CyclingDataBlock(ModbusSequentialDataBlock):
    def __init__(self, address, values):
        super().__init__(address, values)
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self.cycle_values)
        self._thread.daemon = True
        self._thread.start()

    def cycle_values(self):
        for i in range(len(self.values)):
            self.values[i] = (self.values[i] + 1) % 65536


class ModbusServer:
    """MODBUS server class."""

    def __init__(self, host: str, port: int) -> None:
        """Initialize server context and identity."""
        self.storage: ModbusDeviceContext
        self.context: ModbusServerContext
        self.identity: ModbusDeviceIdentification
        self.holding_registers: CyclingDataBlock
        self.input_registers: CyclingDataBlock
        self._mb_server: ModbusTcpServer | None = None
        self.host: str = host
        self.port: int = port
        self.setup_server()

    def setup_server(self) -> None:
        """Run server setup."""
        self.holding_registers = CyclingDataBlock(0x00, [0] * 100)
        self.input_registers = CyclingDataBlock(0x00, [0] * 100)
        self.storage = ModbusDeviceContext(hr=self.holding_registers, ir=self.input_registers)
        self.context = ModbusServerContext(devices=self.storage)

    async def task_cycle_registers(self) -> None:
        """
        Runs continuously to update the first two input registers with the current unix time
        once every second.
        """
        while True:
            self.holding_registers.cycle_values()
            self.input_registers.cycle_values()
            await asyncio.sleep(1)

    async def run_async_server(self) -> None:
        """Run server."""
        print(f"Starting MODBUS TCP server on {self.host}:{self.port}")
        address = (self.host, self.port)
        self._update_timestamp_task = asyncio.create_task(self.task_cycle_registers())
        self._mb_server = ModbusTcpServer(
            context=self.context,  # Data storage
            address=address,  # listen address
        )
        await self._mb_server.serve_forever()

if __name__ == "__main__":
    server = ModbusServer(host="127.0.0.1", port=5020)

    asyncio.run(server.run_async_server())