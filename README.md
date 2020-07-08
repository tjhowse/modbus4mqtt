# Modbus4MQTT
https://github.com/tjhowse/modbus4mqtt
https://pypi.org/project/modbus4mqtt/

![](https://github.com/tjhowse/modbus4mqtt/workflows/Unit%20Tests/badge.svg)
[![codecov](https://codecov.io/gh/tjhowse/modbus4mqtt/branch/master/graph/badge.svg)](https://codecov.io/gh/tjhowse/modbus4mqtt)

This is a gateway that translates between modbus TCP/IP and MQTT.

There are already a few things that do this, but didn't quite have the features I wanted.

The mapping of modbus registers to MQTT topics is in a simple YAML file.

## YAML definition

Look at the [Sungrow SH5k-20](./modbus4mqtt/Sungrow_SH5k_20.yaml) configuration YAML for a working example.

### Modbus device settings
```yaml
ip: 192.168.1.89
port: 502
update_rate: 1
```

This is the address of the Modbus device to be polled. Presently only Modbus TCP/IP is supported. The update rate is in seconds. This defines how frequently the registers are polled for changes.

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
```

This section of the YAML lists all the modbus registers that you consider interesting.

`pub_topic` (Optional) This is the topic to which the value of this register will be published.

`set_topic` (Optional) Values published to this topic will be written to the Modbus device.

`retain` (Optional) Controls whether the value of this register will be published with the retain bit set.

`pub_only_on_change` (Optional) Controls whether this register will only be published if its value changed from the previous poll.

`table` (Optional) The Modbus table to read from the device. Must be 'holding' or 'input'. If absent it will default to 'holding'.

`address` (Required) The address to read from the device.

`value_map` (Optional) A series of human-readable and raw values for the setting. This will be used to translate between human-readable values via MQTT to raw values via Modbus. If a value_map is set for a register the interface will reject raw values sent via MQTT. If value_map is not set the interface will try to set the Modbus register to that value. Note that the scale is applied after the value is read from Modbus and before it is written to Modbus.

`scale` (Optional) After reading a value from the Modbus register it will be multiplied by this scalar before being published to MQTT. Values published on this register's `set_topic` will be divided by this scalar before being written to Modbus.

`mask` (Optional) This is a 16-bit number that can be used to select a part of a Modbus register to be referenced by this configuration entry. For example a mask of `0xFF00` will mean this register will map to the most significant byte of the 16-bit Modbus register at `address`. A mask of `0x0001` will reference only the least significant bit of this register.