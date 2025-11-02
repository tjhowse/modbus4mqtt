from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusSparseDataBlock
import threading
import asyncio
import click
from paho.mqtt import client as mqtt_client

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
        self.holding_registers = ModbusSequentialDataBlock(0x00, [0] * 100)
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

class MQTTClient:
    """ Basic MQTT client """

    def __init__(self, host, port, username=None, password=None):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
        if username and password:
            self.client.username_pw_set(username, password)
        self.client.on_message = self.on_message
        self.client.on_connect = self._on_connect

    def subscribe(self, topic, qos=0):
        self.client.subscribe(topic, qos)

    def publish(self, topic, payload, qos=0):
        self.client.publish(topic, payload, qos)

    def on_message(self, client, userdata, message):
        print(f"Received message on topic {message.topic}: {message.payload.decode()}")

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print("Connected to MQTT.")
        else:
            print("Couldn't connect to MQTT.")
            return

    def connect(self):
        self.client.connect(self.host, self.port)
        self.client.loop_start()

class TestRunner:
    """ This sends commands to modbus and mqtt and ensures values are reported back and forth correctly. """

    def __init__(self, modbus_server: ModbusServer, mqtt_client: MQTTClient):
        self.modbus_server = modbus_server
        self.mqtt_client = mqtt_client
        # Further initialization as needed

    async def run_tests(self):
        # Implement test logic here
        while True:
            await asyncio.sleep(1)
            print("Running tests...")

@click.command()
@click.option('--modbus-host', default='0.0.0.0')
@click.option('--modbus-port', default=5020)
@click.option('--mqtt-host', default='192.168.1.50')
@click.option('--mqtt-port', default=1883)
@click.option('--mqtt-username', default=None)
@click.option('--mqtt-password', default=None)
def main(modbus_host: str, modbus_port: int, mqtt_host: str, mqtt_port: int, mqtt_username: str, mqtt_password: str):
    modbus_server = ModbusServer(host=modbus_host, port=modbus_port)
    mqtt_client = MQTTClient(host=mqtt_host, port=mqtt_port, username=mqtt_username, password=mqtt_password)
    mqtt_client.connect()
    test_runner = TestRunner(modbus_server=modbus_server, mqtt_client=mqtt_client)
    asyncio.run(modbus_server.run_async_server())
    asyncio.run(test_runner.run_tests())

if __name__ == "__main__":
    main()