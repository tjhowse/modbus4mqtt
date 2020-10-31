#!/usr/bin/python3


from pymodbus.client.sync import ModbusTcpClient, ModbusSocketFramer
from SungrowModbusTcpClient import SungrowModbusTcpClient

IP = "192.168.1.89"

mb = SungrowModbusTcpClient.SungrowModbusTcpClient(host=IP, port=502,
                                              framer=ModbusSocketFramer, timeout=1,
                                              RetryOnEmpty=True, retries=1)


result = mb.read_input_registers(5000, 10, unit=0x01)
print(result)
print(result.registers)