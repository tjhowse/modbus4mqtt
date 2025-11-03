from queue import Queue
import pytest
from modbus4mqtt.modbus4mqtt import mqtt_interface
import pytest_asyncio
import random
from time import monotonic
from pymodbus.server import ModbusTcpServer
from pymodbus.datastore import ModbusServerContext, ModbusSequentialDataBlock, ModbusSparseDataBlock
import threading
import asyncio
from paho.mqtt import client as mqtt_client

from pymodbus import ModbusDeviceIdentification
from pymodbus.datastore import (
    ModbusDeviceContext,
    ModbusSequentialDataBlock,
    ModbusServerContext,
)
from pymodbus.server import ModbusTcpServer
import asyncio


class ModbusServer:
    """MODBUS server class."""

    def __init__(self, host: str = '0.0.0.0', port: int = 5020) -> None:
        """Initialize server context and identity."""
        self.storage: ModbusDeviceContext
        self.context: ModbusServerContext
        self.identity: ModbusDeviceIdentification
        self.holding_registers: ModbusSequentialDataBlock
        self.input_registers: ModbusSequentialDataBlock
        self._mb_server: ModbusTcpServer | None = None
        self.host: str = host
        self.port: int = port
        self.setup_server()

    def setup_server(self) -> None:
        """Run server setup."""
        self.holding_registers = ModbusSequentialDataBlock(0x00, [0] * 100)
        self.input_registers = ModbusSequentialDataBlock(0x00, [0] * 100)
        self.storage = ModbusDeviceContext(hr=self.holding_registers, ir=self.input_registers)
        self.context = ModbusServerContext(devices=self.storage)

    async def set_holding_register(self, address: int, value: int) -> None:
        """Set holding register value."""
        self.holding_registers.setValues(address+1, [value])

    async def get_holding_register(self, address: int) -> int:
        """Get holding register value."""
        return self.holding_registers.getValues(address+1, count=1)[0]

    async def run_async_server(self) -> None:
        """Run server."""
        print(f"Starting MODBUS TCP server on {self.host}:{self.port}")
        address = (self.host, self.port)
        self._mb_server = ModbusTcpServer(
            context=self.context,  # Data storage
            address=address,  # listen address
        )
        await self._mb_server.serve_forever()

    async def stop(self) -> None:
        """Stop server."""
        if self._mb_server:
            await self._mb_server.shutdown()
            self._mb_server = None

class MQTTClient:
    """ Basic MQTT client """

    def __init__(self, host: str = '127.0.0.1', port: int = 1883):
        self.host = host
        self.port = port
        self.client = mqtt_client.Client(mqtt_client.CallbackAPIVersion.VERSION2)
        self.client.on_message = self.on_message
        self.client.on_connect = self._on_connect
        self.received_messages: list[tuple[str, str]] = []
        self.connected: asyncio.Event = asyncio.Event()

    def subscribe(self, topic, qos=0):
        self.client.subscribe(topic, qos)

    def unsubscribe_to_all(self):
        self.client.unsubscribe("#")

    def publish(self, topic, payload, qos=0):
        self.client.publish(topic, payload, qos)

    def on_message(self, client, userdata, message):
        print(f"Received message on topic {message.topic}: {message.payload.decode()}")
        self.received_messages.append((message.topic, message.payload.decode()))

    def clear_messages(self):
        self.received_messages = []

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            print(f"Connected to MQTT server on {self.host}:{self.port}.")
            self.connected.set()
        else:
            print("Couldn't connect to MQTT.")
            return

    def connect(self) -> bool:
        print("Connecting to MQTT server...")
        self.client.connect(self.host, self.port)
        self.client.loop_start()
        deadline = monotonic() + 5
        while monotonic() < deadline:
            if self.connected.is_set():
                self.connected.clear()
                return True
        print(f"Failed to connect to MQTT server at {self.host}:{self.port}.")
        exit(1)

class ManualTestRunner:
    """ This sends commands to modbus and mqtt and ensures values are reported back and forth correctly. """

    def __init__(self, modbus_server: ModbusServer, mqtt_client: MQTTClient):
        self.modbus_server = modbus_server
        self.mqtt_client = mqtt_client
        # Further initialization as needed

    async def run_tests(self):
        # Implement test logic here
        self.mqtt_client.subscribe("tests/holding")
        deadline = monotonic() + 1
        while len(self.mqtt_client.received_messages) == 0 and monotonic() < deadline:
            await asyncio.sleep(0.1)
        self.mqtt_client.clear_messages()

        i = random.randint(1, 1000)
        while True:
            await asyncio.sleep(1)
            print("Running tests...")
            print(f"Setting holding register 1 to {i}")
            await self.modbus_server.set_holding_register(1, i)
            print("Waiting for MQTT messages...")
            while len(self.mqtt_client.received_messages) == 0:
                await asyncio.sleep(0.1)
            message = self.mqtt_client.received_messages.pop(0)
            print("Test completed, received messages:", message)
            i += 1

async def async_main(modbus_server: ModbusServer, test_runner: ManualTestRunner):
    tasks = [
        asyncio.create_task(modbus_server.run_async_server()),
        asyncio.create_task(test_runner.run_tests())
    ]
    await asyncio.gather(*tasks)

def main():
    modbus_server = ModbusServer()
    mqtt_client = MQTTClient()
    mqtt_client.connect()
    test_runner = ManualTestRunner(modbus_server=modbus_server, mqtt_client=mqtt_client)
    asyncio.run(async_main(modbus_server, test_runner))


@pytest_asyncio.fixture
async def modbus_fixture():
    modbus_server = ModbusServer()
    server_task = asyncio.create_task(modbus_server.run_async_server())
    await asyncio.sleep(1)  # Give server time to start
    try:
        yield modbus_server
        await modbus_server.stop()
    finally:
        server_task.cancel()
        try:
            await server_task
        except asyncio.CancelledError:
            pass


@pytest.fixture
def mqtt_fixture():
    mqtt_client = MQTTClient()
    mqtt_client.connect()
    yield mqtt_client

@pytest.fixture
def modbus4mqtt_fixture():

    app = mqtt_interface(   hostname='127.0.0.1',
                            port=1883,
                            username='',
                            password='',
                            use_tls=False,
                            config_file='tests/test_integration.yaml',
                            mqtt_topic_prefix='tests')
    app.connect()
    threading.Thread(target=app.loop_forever, daemon=True).start()
    yield app
    app.stop()

async def wait_for_mqtt_messages(mqtt_client: MQTTClient, timeout: float = 1.0, count: int = 1) -> list[tuple[str, str]]:
    deadline = monotonic() + timeout
    while len(mqtt_client.received_messages) < count and monotonic() < deadline:
        await asyncio.sleep(0.1)
    if len(mqtt_client.received_messages) < count:
        raise TimeoutError(f"Did not receive {count} MQTT messages within timeout period.")
    return mqtt_client.received_messages

@pytest.mark.asyncio
async def test_basic_functionality(modbus_fixture: ModbusServer, mqtt_fixture: MQTTClient, modbus4mqtt_fixture: mqtt_interface):
    mqtt_fixture.subscribe("tests/holding")
    test_number = random.randint(1, 0xFFFF)
    await modbus_fixture.set_holding_register(1, test_number)
    message = await wait_for_mqtt_messages(mqtt_fixture)
    topic, payload = message[0]
    assert topic == "tests/holding"
    assert int(payload) == test_number

@pytest.mark.asyncio
async def test_multibyte_registers(modbus_fixture: ModbusServer, mqtt_fixture: MQTTClient, modbus4mqtt_fixture: mqtt_interface):
    mqtt_fixture.subscribe("tests/uint64")
    mqtt_fixture.subscribe("tests/overlapping_uint16")
    test_number = 0x1234567890ABCDEF
    # Set the uint64 value across 4 registers (assuming big-endian)
    await modbus_fixture.set_holding_register(10, (test_number >> 48) & 0xFFFF)
    await modbus_fixture.set_holding_register(11, (test_number >> 32) & 0xFFFF)
    await modbus_fixture.set_holding_register(12, (test_number >> 16) & 0xFFFF)
    await modbus_fixture.set_holding_register(13, test_number & 0xFFFF)
    messages = await wait_for_mqtt_messages(mqtt_fixture, count=2)
    for message in messages:
        topic, payload = message
        if topic == "tests/uint64":
            assert int(payload) == test_number
        elif topic == "tests/overlapping_uint16":
            # Overlapping uint16 register at address 10 should contain the high word of the uint64
            expected_value = (test_number >> 48) & 0xFFFF
            assert int(payload) == expected_value


@pytest.mark.asyncio
async def test_mqtt_write(modbus_fixture: ModbusServer, mqtt_fixture: MQTTClient, modbus4mqtt_fixture: mqtt_interface):
    test_number = random.randint(1, 0xFFFF)
    mqtt_fixture.publish("tests/holding/set", str(test_number))
    deadline = monotonic() + 3
    while monotonic() < deadline:
        await asyncio.sleep(0.1)  # Give time for the message to be processed
        holding_value = await modbus_fixture.get_holding_register(1)
        if holding_value == test_number:
            break
    else:
        assert False, "Timeout waiting for Modbus register to update"

if __name__ == "__main__":
    main()