#!/usr/bin/python3

from time import sleep, monotonic
from datetime import datetime
import json
import logging
from ruamel.yaml import YAML
import click
import paho.mqtt.client as mqtt

from . import modbus_interface
import importlib.metadata
_version = importlib.metadata.version("modbus4mqtt")

MAX_DECIMAL_POINTS = 8

# Modbus connection status enum
class ModbusConnectionStatus:
    Offline = "offline"
    Online = "online"
    Connecting = "connecting"


class mqtt_interface():
    def __init__(   self,
                    hostname: str,
                    port: int,
                    username: str,
                    password: str,
                    config_file: str,
                    mqtt_topic_prefix: str,
                    use_tls=True,
                    insecure=False,
                    cafile=None,
                    cert=None,
                    key=None):
        self._running = True
        self.hostname = hostname
        self._port = port
        self.username = username
        self.password = password
        self.config = self._load_modbus_config(config_file)
        self.use_tls = use_tls
        self.insecure = insecure
        self.cafile = cafile
        self.cert = cert
        self.key = key
        if not mqtt_topic_prefix.endswith('/'):
            mqtt_topic_prefix = mqtt_topic_prefix + '/'
        self.prefix = mqtt_topic_prefix
        self.address_offset = self.config.get('address_offset', 0)
        self.registers = self.config['registers']
        for register in self.registers:
            register['address'] += self.address_offset
        self.modbus_connect_retries = -1  # Retry forever by default
        self.modbus_reconnect_sleep_interval = 5  # Wait this many seconds between modbus connection attempts
        self.modbus_connection_status: ModbusConnectionStatus = ModbusConnectionStatus.Offline
        self.setup_modbus()

    def connect(self):
        # Connects to modbus and MQTT.
        self.connect_mqtt()
        self.connect_modbus()

    def setup_modbus(self):
        if self.config.get('word_order', 'highlow').lower() == 'lowhigh':
            word_order = modbus_interface.WordOrder.LowHigh
        else:
            word_order = modbus_interface.WordOrder.HighLow

        if self.config.get('write_mode', 'multi').lower() == 'multi':
            write_mode = modbus_interface.WriteMode.Multi
        else:
            write_mode = modbus_interface.WriteMode.Single

        self._mb = modbus_interface.modbus_interface(ip=self.config['ip'],
                                                     port=self.config.get('port', 502),
                                                     device_address=self.config.get('device_address', 0x01),
                                                     write_mode=write_mode,
                                                     variant=self.config.get('variant', None),
                                                     # Allow the use of the deprecated "scan_batching" config option for backwards compatibility
                                                     read_batching=self.config.get('read_batching', self.config.get('scan_batching', None)),
                                                     word_order=word_order)
        # Tells the modbus interface about the registers we consider interesting.
        for register in self.registers:
            self._mb.add_monitor_register(register.get('table', 'holding'), register['address'], register.get('type', 'uint16'))
            register['value'] = None

    def connect_modbus(self):
        self.set_modbus_connection_status(ModbusConnectionStatus.Connecting)
        logging.info("Connecting to Modbus...")
        if self._mb.connect():
            logging.info("Connected to Modbus.")
            self.set_modbus_connection_status(ModbusConnectionStatus.Online)
        else:
            self.set_modbus_connection_status(ModbusConnectionStatus.Offline)


    def set_modbus_connection_status(self, status: ModbusConnectionStatus):
        if status == self.modbus_connection_status:
            return
        self.modbus_connection_status = status
        if not self._mqtt_client.is_connected():
            return
        self._mqtt_client.publish(self.prefix + 'modbus4mqtt/modbus_status',
                                    json.dumps({
                                        "status": self.modbus_connection_status,
                                        "timestamp": datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
                                    }),
                                    retain=True,
                                    )

    def modbus_connection_failed(self):
        exit(1)

    def connect_mqtt(self):
        self._mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self._mqtt_client.username_pw_set(self.username, self.password)
        self._mqtt_client._on_connect = self._on_connect
        self._mqtt_client._on_disconnect = self._on_disconnect
        self._mqtt_client._on_message = self._on_message
        if self.use_tls:
            self._mqtt_client.tls_set(ca_certs=self.cafile, certfile=self.cert, keyfile=self.key)
            self._mqtt_client.tls_insecure_set(self.insecure)
        lwt_message = json.dumps({
            "status": "offline",
            "version": f"v{_version}",
            "timestamp": datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
        })
        self._mqtt_client.will_set(self.prefix + 'modbus4mqtt', lwt_message, retain=True)
        self._mqtt_client.connect(self.hostname, self._port, 60)
        self._mqtt_client.loop_start()

    def _get_registers_with(self, required_key):
        # Returns the registers containing the required_key
        return [register for register in self.registers if required_key in register]

    def poll(self):
        try:
            self._mb.poll()
            self.set_modbus_connection_status(ModbusConnectionStatus.Online)
        except Exception as e:
            logging.error("Failed to poll modbus device, attempting to reconnect: {}".format(e))
            self.set_modbus_connection_status(ModbusConnectionStatus.Offline)
            self.connect_modbus()
            return

        # This is used to store values that are published as JSON messages rather than individual values
        json_messages = {}
        json_messages_retain = {}

        for register in self._get_registers_with('pub_topic'):
            try:
                value = self._mb.get_value( register.get('table', 'holding'),
                                            register['address'],
                                            register.get('type', 'uint16'))
            except Exception:
                logging.warning("Couldn't get value from register {} in table {}".format(register['address'],
                                register.get('table', 'holding')))
                continue
            # Filter the value through the mask, if present.
            if 'mask' in register:
                # masks only make sense for uint
                if register.get('type', 'uint16') in ['uint16', 'uint32', 'uint64']:
                    value &= register.get('mask')
            # Scale the value, if required.
            value *= register.get('scale', 1)
            # Clamp the number of decimal points
            value = round(value, MAX_DECIMAL_POINTS)
            changed = False
            if value != register['value']:
                changed = True
                register['value'] = value
            if not changed and register.get('pub_only_on_change', True):
                continue
            # Map from the raw number back to the human-readable form
            if 'value_map' in register:
                if value in register['value_map'].values():
                    # This is a bit weird...
                    value = [human for human, raw in register['value_map'].items() if raw == value][0]
            if register.get('json_key', False):
                # This value won't get published to MQTT immediately. It gets stored and sent at the end of the poll.
                if register['pub_topic'] not in json_messages:
                    json_messages[register['pub_topic']] = {}
                    json_messages_retain[register['pub_topic']] = False
                json_messages[register['pub_topic']][register['json_key']] = value
                if 'retain' in register:
                    json_messages_retain[register['pub_topic']] = register['retain']
            else:
                retain = register.get('retain', False)
                self._mqtt_client.publish(self.prefix+register['pub_topic'], value, retain=retain)

        # Transmit the queued JSON messages.
        for topic, message in json_messages.items():
            m = json.dumps(message, sort_keys=True)
            self._mqtt_client.publish(self.prefix+topic, m, retain=json_messages_retain[topic])

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            logging.info("Connected to MQTT.")
        else:
            logging.error("Couldn't connect to MQTT.")
            return
        # Subscribe to all the set topics.
        for register in self._get_registers_with('set_topic'):
            self._mqtt_client.subscribe(self.prefix+register['set_topic'])
            print("Subscribed to {}".format(self.prefix+register['set_topic']))
        self._mqtt_client.publish(self.prefix+'modbus4mqtt',
                                  json.dumps({
                                        "status": "online",
                                        "version": f"{_version}",
                                        "timestamp": datetime.now().astimezone().strftime('%Y-%m-%dT%H:%M:%S%z')
                                  }),
                                  retain=True
                                  )

    def _on_disconnect(self, client, userdata, flags, reason_code, properties):
        logging.warning("Disconnected from MQTT. Attempting to reconnect.")

    def _on_message(self, client, userdata, msg):
        # print("got a message: {}: {}".format(msg.topic, msg.payload))
        # TODO Handle json_key writes. https://github.com/tjhowse/modbus4mqtt/issues/23
        topic = msg.topic[len(self.prefix):]
        for register in [register for register in self.registers if 'set_topic' in register]:
            if topic != register['set_topic']:
                continue
            # We received a set topic message for this topic.
            value = msg.payload
            if 'value_map' in register:
                try:
                    value = str(value, 'utf-8')
                    if value not in register['value_map']:
                        logging.warning("Value not in value_map. Topic: {}, value: {}, valid values: {}".format(topic,
                                        value, register['value_map'].keys()))
                        continue
                    # Map the value from the human-readable form into the raw modbus number
                    value = register['value_map'][value]
                except UnicodeDecodeError:
                    logging.warning("Failed to decode MQTT payload as UTF-8. "
                                    "Can't compare it to the value_map for register {}".format(register))
                    continue
            try:
                # Scale the value, if required.
                value = float(value)
                value = round(value/register.get('scale', 1))
            except ValueError:
                logging.error("Failed to convert register value for writing. "
                              "Bad/missing value_map? Topic: {}, Value: {}".format(topic, value))
                continue
            type = register.get('type', 'uint16')
            self._mb.set_value(register.get('table', 'holding'), register['address'], int(value),
                               register.get('mask', 0xFFFF), type)

    # This throws ValueError exceptions if the imported registers are invalid
    @staticmethod
    def _validate_registers(registers):
        all_pub_topics = set()
        duplicate_pub_topics = set()
        # Key: shared pub_topics, value: list of json_keys
        duplicate_json_keys = {}
        # Key: shared pub_topics, value: set of retain values (true/false)
        retain_setting = {}
        valid_types = ['uint16', 'int16', 'uint32', 'int32', 'uint64', 'int64']

        # Look for duplicate pub_topics
        for register in registers:
            type = register.get('type', 'uint16')
            if type not in valid_types:
                raise ValueError("Bad YAML configuration. Register has invalid type '{}'.".format(type))
            if register['pub_topic'] in all_pub_topics:
                duplicate_pub_topics.add(register['pub_topic'])
                duplicate_json_keys[register['pub_topic']] = []
                retain_setting[register['pub_topic']] = set()
            if 'json_key' in register and 'set_topic' in register:
                raise ValueError("Bad YAML configuration. Register with set_topic '{}' has a json_key specified. "
                                 "This is invalid. See https://github.com/tjhowse/modbus4mqtt/issues/23 for details."
                                 .format(register['set_topic']))
            all_pub_topics.add(register['pub_topic'])

        # Check that all registers with duplicate pub topics have json_keys
        for register in registers:
            if register['pub_topic'] in duplicate_pub_topics:
                if 'json_key' not in register:
                    raise ValueError("Bad YAML configuration. pub_topic '{}' duplicated across registers without "
                                     "json_key field. Registers that share a pub_topic must also have a unique "
                                     "json_key.".format(register['pub_topic']))
                if register['json_key'] in duplicate_json_keys[register['pub_topic']]:
                    raise ValueError("Bad YAML configuration. pub_topic '{}' duplicated across registers with a "
                                     "duplicated json_key field. Registers that share a pub_topic must also have "
                                     "a unique json_key.".format(register['pub_topic']))
                duplicate_json_keys[register['pub_topic']] += [register['json_key']]
                if 'retain' in register:
                    retain_setting[register['pub_topic']].add(register['retain'])
        # Check that there are no disagreements as to whether this pub_topic should be retained or not.
        for topic, retain_set in retain_setting.items():
            if len(retain_set) > 1:
                raise ValueError("Bad YAML configuration. pub_topic '{}' has conflicting retain settings."
                                 .format(topic))

    def _load_modbus_config(self, path):
        yaml = YAML(typ='safe')
        result = yaml.load(open(path, 'r').read())
        registers = [register for register in result['registers'] if 'pub_topic' in register]
        mqtt_interface._validate_registers(registers)
        return result

    def loop_forever(self):
        while self._running:
            next_update_time_s = monotonic() + self.config['update_rate']
            self.poll()
            sleep(max(0, next_update_time_s - monotonic()))

    def stop(self):
        self._running = False
        self._mqtt_client.loop_stop()
        self._mqtt_client.disconnect()
        self._mb.close()



@click.command()
@click.option('--hostname', default='localhost',
              help='The hostname or IP address of the MQTT server.', show_default=True)
@click.option('--port', default=1883,
              help='The port of the MQTT server.', show_default=True)
@click.option('--username', default='username',
              help='The username to authenticate to the MQTT server.', show_default=True)
@click.option('--password', default='password',
              help='The password to authenticate to the MQTT server.', show_default=True)
@click.option('--mqtt_topic_prefix', default='modbus4mqtt',
              help='A prefix for published MQTT topics.', show_default=True)
@click.option('--config', default='./Sungrow_SH5k_20.yaml',
              help='The YAML config file for your modbus device.', show_default=True)
@click.option('--use_tls', default=False,
              help='Configure network encryption and authentication options. Enables SSL/TLS.', show_default=True)
@click.option('--insecure', default=True,
              help='Do not check that the server certificate hostname matches the remote hostname.', show_default=True)
@click.option('--cafile', default=None,
              help='The path to a file containing trusted CA certificates to enable encryption.', show_default=True)
@click.option('--cert', default=None,
              help='Client certificate for authentication, if required by server.', show_default=True)
@click.option('--key', default=None,
              help='Client private key for authentication, if required by server.', show_default=True)
def main(hostname, port, username, password, config, mqtt_topic_prefix, use_tls, insecure, cafile, cert, key):
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S')
    logging.info("Starting modbus4mqtt v{}".format(_version))
    i = mqtt_interface(hostname, port, username, password, config, mqtt_topic_prefix,
                       use_tls, insecure, cafile, cert, key)
    i.connect()
    i.loop_forever()


if __name__ == '__main__':
    main()
