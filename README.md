# Modbus4MQTT
https://github.com/tjhowse/modbus4mqtt

https://pypi.org/project/modbus4mqtt/

![](https://github.com/tjhowse/modbus4mqtt/workflows/Unit%20Tests/badge.svg)

[![codecov](https://codecov.io/gh/tjhowse/modbus4mqtt/branch/master/graph/badge.svg)](https://codecov.io/gh/tjhowse/modbus4mqtt)

This is a gateway that translates between modbus TCP/IP and MQTT.

There are already a few things that do this, but didn't quite have the features I wanted.

The mapping of modbus registers to MQTT topics is in a simple YAML file.

The most up-to-date docs will always be on Github.

## Installation

```bash
$ pip3 install --user modbus4mqtt
$ modbus4mqtt --help
```

Alternatively you can run Modbus4MQTT in a Docker container. A [Dockerfile](./Dockerfile) example is provided.

You will need to provide the credentials to connect to your MQTT broker, as well as a path to a YAML file that defines the memory map of your Modbus device.

## YAML definition

Look at the [Sungrow SH5k-20](./modbus4mqtt/Sungrow_SH5k_20.yaml) configuration YAML for a working example.

### Modbus device settings
```yaml
ip: 192.168.1.89
port: 502
update_rate: 5
address_offset: 0
variant: sungrow
```

`ip` (Required) The IP address of the modbus device to be polled. Presently only modbus TCP/IP is supported.

`port` (Optional: default 502) The port on the modbus device to connect to.

`update_rate` (Optional: default 5) The number of seconds between polls of the modbus device.

`address_offset` (Optional: default 0) This offset is applied to every register address to accomodate different Modbus addressing systems. In many Modbus devices the first register is enumerated as 1, other times 0. See section 4.4 of the Modbus spec.

`variant` (Optional) Allows variants of the ModbusTcpClient library to be used. Setting this to 'sungrow' enables support of SungrowModbusTcpClient. This library transparently decrypts the modbus comms with sungrow SH inverters running newer firmware versions.

```yaml
registers:
  - pub_topic: "forced_charge/mode"
    set_topic: "forced_charge/mode/set"
    retain: true
    pub_only_on_change: false
    table: 'holding'
    address: 13140
    value_map:
      enabled: 170
      disabled: 85
  - pub_topic: "forced_charge/period_1/start_hours"
    set_topic: "forced_charge/period_1/start_hours/set"
    pub_only_on_change: true
    table: 'holding'
    address: 13142
  - pub_topic: "voltage_in_mv"
    address: 13000
    scale: 1000
  - pub_topic: "first_bit_of_second_byte"
    address: 13001
    mask: 0x0010
```

This section of the YAML lists all the modbus registers that you consider interesting.

`address` (Required) The decimal address of the register to read from the device, starting at 0. Many modbus devices enumerate registers beginning at 1, so beware.

`pub_topic` (Optional) This is the topic to which the value of this register will be published.

`set_topic` (Optional) Values published to this topic will be written to the Modbus device.

`retain` (Optional) Controls whether the value of this register will be published with the retain bit set.

`pub_only_on_change` (Optional) Controls whether this register will only be published if its value changed from the previous poll. Defaults to True.

`table` (Optional) The Modbus table to read from the device. Must be 'holding' or 'input'. If absent it will default to 'holding'.

`value_map` (Optional) A series of human-readable and raw values for the setting. This will be used to translate between human-readable values via MQTT to raw values via Modbus. If a value_map is set for a register the interface will reject raw values sent via MQTT. If value_map is not set the interface will try to set the Modbus register to that value. Note that the scale is applied after the value is read from Modbus and before it is written to Modbus.

`scale` (Optional) After reading a value from the Modbus register it will be multiplied by this scalar before being published to MQTT. Values published on this register's `set_topic` will be divided by this scalar before being written to Modbus.

`mask` (Optional) This is a 16-bit number that can be used to select a part of a Modbus register to be referenced by this register. For example a mask of `0xFF00` will map to the most significant byte of the 16-bit Modbus register at `address`. A mask of `0x0001` will reference only the least significant bit of this register.