# Modbus4MQTT

https://github.com/tjhowse/modbus4mqtt

https://pypi.org/project/modbus4mqtt/

![](https://github.com/tjhowse/modbus4mqtt/workflows/Unit%20Tests/badge.svg)

[![codecov](https://codecov.io/gh/tjhowse/modbus4mqtt/branch/master/graph/badge.svg)](https://codecov.io/gh/tjhowse/modbus4mqtt)

This is a gateway that translates between modbus TCP/IP and MQTT.

The mapping of modbus registers to MQTT topics is in a simple YAML file.

The most up-to-date docs will always be on Github.

## Similar software

There is already good software out there that can do what Modbus4MQTT does, but none
that I could find that has this functionality as its focus. Do one thing and do it well.
Modbus4mqtt is designed to fit in as a component of other systems, rather than trying to
be a complete solution.

* [Solariot](https://github.com/meltaxa/solariot)
* [PVStats](https://github.com/ptarcher/pvstats) ([Fork](https://github.com/tjhowse/pvstats))

## Installation

### Python module

```bash
$ pip3 install --user modbus4mqtt
$ modbus4mqtt --help
```

### Docker container

Alternatively you can run Modbus4MQTT in a Docker container. A [Dockerfile](./Dockerfile) example is provided.

```bash
$ docker pull tjhowse/modbus4mqtt:latest
$ docker run modbus4mqtt --help
```
When launching inside the docker container you will either need to use one of the built-in YAMLs like `/modbus4mqtt/modbus4mqtt/Sungrow_SH5k_20.yaml`, or map your custom YAML into the container in a volume.

## YAML definition

Look at the [Sungrow SH5k-20](./modbus4mqtt/Sungrow_SH5k_20.yaml) configuration YAML for a working example.

### Modbus device settings
```yaml
ip: 192.168.1.89
port: 502
update_rate: 5
address_offset: 0
variant: sungrow
scan_batching: 100
word_order: highlow
```
| Field name | Required | Default | Description |
| ---------- | -------- | ------- | ----------- |
| ip | Required | N/A | The IP address of the modbus device to be polled. Presently only modbus TCP/IP is supported. |
| port | Optional | 502 | The port on the modbus device to connect to. |
| update_rate | Optional | 5 | The number of seconds between polls of the modbus device. |
| address_offset | Optional | 0 | This offset is applied to every register address to accommodate different Modbus addressing systems. In many Modbus devices the first register is enumerated as 1, other times 0. See section 4.4 of the Modbus spec. |
| variant | Optional | N/A | Allows variants of the ModbusTcpClient library to be used. Setting this to 'sungrow' enables support of SungrowModbusTcpClient. This library transparently decrypts the modbus comms with sungrow SH inverters running newer firmware versions. |
| scan_batching | Optional | 100 | Must be between 1 and 100 inclusive. Modbus read operations are more efficient in bigger batches of contiguous registers, but different devices have different limits on the size of the batched reads. This setting can also be helpful when building a modbus register map for an uncharted device. In some modbus devices a single invalid register in a read range will fail the entire read operation. By setting `scan_batching` to `1` each register will be scanned individually. This will be very inefficient and should not be used in production as it will saturate the link with many read operations. |
| word_order | Optional | 'highlow' | Must be either `highlow` or `lowhigh`. This determines how multi-word values are interpreted. `highlow` means a 32-bit number at address 1 will have its high two bytes stored in register 1, and its low two bytes stored in register 2. The default is typically correct, as modbus has a big-endian memory structure, but this is not universal. |

### Register settings
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
  - pub_topic: "load_control/optimized/end_time"
    address: 13013
    json_key: hours
  - pub_topic: "load_control/optimized/end_time"
    address: 13014
    json_key: minutes
  - pub_topic: "external_temperature"
    address: 13015
    type: int16
  - pub_topic: "minutes_online"
    address: 13016
    type: uint32
```

This section of the YAML lists all the modbus registers that you consider interesting.

| Field name | Required | Default | Description |
| ---------- | -------- | ------- | ----------- |
| address | Required | N/A | The decimal address of the register to read from the device, starting at 0. Many modbus devices enumerate registers beginning at 1, so beware. |
| pub_topic | Optional | N/A | This is the topic to which the value of this register will be published. |
| set_topic | Optional | N/A | Values published to this topic will be written to the Modbus device. Cannot yet be combined with json_key. See https://github.com/tjhowse/modbus4mqtt/issues/23 for details. |
| retain | Optional | false | Controls whether the value of this register will be published with the retain bit set. |
| pub_only_on_change | Optional | true | Controls whether this register will only be published if its value changed from the previous poll. |
| table | Optional | holding | The Modbus table to read from the device. Must be 'holding' or 'input'. |
| value_map | Optional | N/A | A series of human-readable and raw values for the setting. This will be used to translate between human-readable values via MQTT to raw values via Modbus. If a value_map is set for a register the interface will reject raw values sent via MQTT. If value_map is not set the interface will try to set the Modbus register to that value. Note that the scale is applied after the value is read from Modbus and before it is written to Modbus. |
| scale | Optional | 1 | After reading a value from the Modbus register it will be multiplied by this scalar before being published to MQTT. Values published on this register's `set_topic` will be divided by this scalar before being written to Modbus. |
| mask | Optional | 0xFFFF | This is a 16-bit number that can be used to select a part of a Modbus register to be referenced by this register. For example a mask of `0xFF00` will map to the most significant byte of the 16-bit Modbus register at `address`. A mask of `0x0001` will reference only the least significant bit of this register. |
| json_key | Optional | N/A | The value of this register will be published to its pub_topic in JSON format. E.G. `{ key: value }` Registers with a json_key specified can share a pub_topic. All registers with shared pub_topics must have a json_key specified. In this way, multiple registers can be published to the same topic in a single JSON message. If any of the registers that share a pub_topic have the retain field set that will affect the published JSON message. Conflicting retain settings are invalid. The keys will be alphabetically sorted. |
| type | Optional | uint16 | The type of the value stored at the modbus address provided. Only uint16 (unsigned 16-bit integer), int16 (signed 16-bit integer), uint32, int32, uint64 and int64 are currently supported. |
