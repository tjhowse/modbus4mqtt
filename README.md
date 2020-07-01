# Modbus4MQTT
https://github.com/tjhowse/modbus4mqtt

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

`table` (Required) The Modbus table to read from the device. Must be 'holding' or 'input'.

`address` (Required) The address to read from the device.

`value_map` (Optional) A list of human-readable and raw values for the setting. This will be used to translate between human-readable values via
MQTT to raw values via Modbus.

