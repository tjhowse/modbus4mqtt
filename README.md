# Modbus4MQTT
this is my rebuild of my fork from a fork from a fork from modbus4mqtt by tjhowse.
I have added Double (float64), multiple servicefiles, singlerun option (default) , 
cleaned up wrong templates. updated readme.


##### thanks to tjhowse for the main project
##### thanks to JPSteindlberger for float support
##### thanks to a-s-z-home for float support and modbus_interface with units and serial support

#
https://github.com/tjhowse/modbus4mqtt

https://pypi.org/project/modbus4mqtt/


## Installation

```bash
git clone https://github.com/Pubaluba/modbus4mqtt_rebuild
cd modbus4mqtt_rebuild

apt install python3-pip
pip3 install -r requirements.txt


#move the service file to systemd
mv *.service /etc/systemd/system/
systemctl daemon-reload

#install the python file to dist
python3 --version

if 3.6.*
mkdir /usr/local/lib/python3.6/dist-packages/modbus4mqtt
mv *.py /usr/local/lib/python3.6/dist-packages/modbus4mqtt

if 3.10.*
mkdir /usr/local/lib/python3.10/dist-packages/modbus4mqtt
mv *.py /usr/local/lib/python3.10/dist-packages/modbus4mqtt


#move the binary 
mv modbus4mqtt /usr/local/bin/
chmod 777 /usr/local/bin/modbus4mqtt

#move the remaining to configdir
mkdir /etc/modbus4mqtt
mv * /etc/modbus4mqtt

cd  /etc/modbus4mqtt/
modbus4mqtt --help

test your config (example:)
modbus4mqtt --mqtt_topic_prefix "***" --hostname "***" --config /etc/modbus4mqtt/TCPRTU1.yaml

if your want to run in loop, use "--loop True" else a singlerun will be performed to preserve correct timestamps
unse 
```
## use cron for singlerun:
```bash
crontab -e 
```
you can use cron every 5 min for yield data: 
##### */5 * * * *  /etc/modbus4mqtt/autorunconfig

you can use cron every 10 sec for power data  (one start more !)
##### */10 * * * * * /etc/modbus4mqtt/autorunpro

both scripts use the servicefiles modbus4mqttconfig@ / modbusmqttpro@.service


## to use a service with "updaterate" setting:
please be awere of inconsistent timestamp. running multiple services @ different devices will loop different because a simple sllep command is used after polling.

copy a ./template/.yaml file to /etc/modbus4mqtt/config/ 
```bash
systemctl start modbusmqttloop@"yourfile(include!.yaml)" 

systemctl status modbusmqttloop@"yourfile(include!.yaml)"

systemctl enable modbusmqttloop@"yourfile(include!.yaml)"
```
the service uses the hostname as prefix

u can change this by editing the service file in /etc/systemd/system/
the autostart file can be used instead:
every .yaml in /etc/modbus4mqtt/config will be started be this script. use cron or similar 


## use the commandline
```bash
modbus4mqtt --help
```
make you own command, script, servicefile, etc

# Yaml Configuration

### Modbus device settings
```yaml

url: tcp://192.168.1.89:502
#url: serial:///dev/ttyUSB1?comset=8E1
update_rate: 30  # only used for "--loop True"
address_offset: 0 
scan_batching: 100
word_order: highlow
```
| Field name | Required | Default | Description |
| ---------- | -------- | ------- | ----------- |
|url | Required | N/A | The  address of the modbus device to be polled. modbus TCP/IP and serial is supported. I suggest using mbusd https://github.com/3cky/mbusd for serial devices if you want to pull multiple data with different rates. Serial does not support multiple clients when opend |
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
    unit: 8  ## defaults to 1
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

  - pub_topic: "Yield"
    json_key: "PV inverter"    # use "" for spaces
    address: 53
    unit: 8
    type: float

  - pub_topic: "Yield"
    json_key: Windturbine
    address: 210
    unit: 255
    type: double

  - 


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
| mask | Optional | 0xFFFF | This is a 16-bit number that can be used to select a part of a Modbus register to be referenced by this register. For example a mask of `0xFF00` will map to the most significant byte of the 16-bit Modbus register at `address`. A mask of `0x0001` will reference only the least significant bit of this register. only to be used @ unsigned integer 16/32/64 bit |
| json_key | Optional | N/A | The value of this register will be published to its pub_topic in JSON format. E.G. `{ key: value }` Registers with a json_key specified can share a pub_topic. All registers with shared pub_topics must have a json_key specified. In this way, multiple registers can be published to the same topic in a single JSON message. If any of the registers that share a pub_topic have the retain field set that will affect the published JSON message. Conflicting retain settings are invalid. The keys will be alphabetically sorted. Keys with spaces must use ".. .." |
| type | Optional | uint16 | The type of the value stored at the modbus address provided. uint16 (unsigned 16-bit integer), int16 (signed 16-bit integer), uint32, int32, uint64 and int64 , float , float_be (big Endian), float_le (little Endian) double, double_be, double_le are supported. |
