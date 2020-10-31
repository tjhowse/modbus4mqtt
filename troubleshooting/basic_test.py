#!/usr/bin/python3

from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer
from SungrowModbusTcpClient import SungrowModbusTcpClient

IP = "192.168.1.89"

mb = SungrowModbusTcpClient.SungrowModbusTcpClient(host=IP, port=502,
                                              framer=ModbusSocketFramer, timeout=1,
                                              RetryOnEmpty=True, retries=1)

print("Basline test. Read 10 register starting at 5000.")
result = mb.read_holding_registers(5000, 10, unit=0x01)
print(result)
print(result.registers)
result = mb.read_input_registers(5000, 10, unit=0x01)
print(result)
print(result.registers)

print("Basline test. Read 100 register starting at 5000.")
result = mb.read_holding_registers(5000, 100, unit=0x01)
print(result)
print(result.registers)
result = mb.read_input_registers(5000, 100, unit=0x01)
print(result)
print(result.registers)

mb.close()

from modbus4mqtt import modbus_interface

mb2 = modbus_interface.modbus_interface(IP, 502, 5, "sungrow")
mb2.connect()

for i in range(10):
    mb2.add_monitor_register("holding", 5000+i)
    mb2.add_monitor_register("input", 5000+1)

print("modbus_interface test. Read 10 registers starting at 5000.")
mb2.poll()
a = []
b = []
for i in range(10):
    a += [mb2.get_value("holding", 5000+i)]
    b += [mb2.get_value("input", 5000+i)]
print(a)
print(b)


print("modbus_interface test. Read 100 registers starting at 5000.")
mb2.add_monitor_register("holding", 5099)
mb2.add_monitor_register("input", 5099)

mb2.poll()
a = []
b = []
for i in range(100):
    a += [mb2.get_value("holding", 5000+i)]
    b += [mb2.get_value("input", 5000+i)]
print(a)
print(b)