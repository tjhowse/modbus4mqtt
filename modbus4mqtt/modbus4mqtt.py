#!/usr/bin/python3

from time import sleep
import logging
import yaml
import click
import paho.mqtt.client as mqtt
from . import modbus_interface

class mqtt_interface():
    def __init__(self, hostname, port, username, password, config_file, mqtt_topic_prefix):
        self.hostname = hostname
        self._port = port
        self.username = username
        self.password = password
        self.config = self._load_modbus_config(config_file)
        if not mqtt_topic_prefix.endswith('/'):
            mqtt_topic_prefix = mqtt_topic_prefix + '/'
        self.prefix = mqtt_topic_prefix
        self.address_offset = self.config.get('address_offset', 0)
        self.registers = self.config['registers']
        for register in self.registers:
            register['address'] += self.address_offset
        self.modbus_connect_retries = -1 # Retry forever by default
        self.modbus_reconnect_sleep_interval = 5 # Wait this many seconds between modbus connection attempts

    def connect(self):
        # Connects to modbus and MQTT.
        self.connect_modbus()
        self.connect_mqtt()

    def connect_modbus(self):
        self._mb = modbus_interface.modbus_interface(self.config['ip'],
                                                     self.config.get('port', 502),
                                                     self.config.get('update_rate', 5),
                                                     variant=self.config.get('variant', None))
        failed_attempts = 1
        while self._mb.connect():
            logging.warning("Modbus connection attempt {} failed. Retrying...".format(failed_attempts))
            failed_attempts += 1
            if self.modbus_connect_retries != -1 and failed_attempts > self.modbus_connect_retries:
                logging.error("Failed to connect to modbus. Giving up.")
                self.modbus_connection_failed()
                # This weird break is here because we mock out modbus_connection_failed in the tests
                break
            sleep(self.modbus_reconnect_sleep_interval)
        # Tells the modbus interface about the registers we consider interesting.
        for register in self.registers:
            self._mb.add_monitor_register(register.get('table', 'holding'), register['address'])
            register['value'] = None

    def modbus_connection_failed(self):
        exit(1)

    def connect_mqtt(self):
        self._mqtt_client = mqtt.Client()
        self._mqtt_client.username_pw_set(self.username, self.password)
        self._mqtt_client._on_connect = self._on_connect
        self._mqtt_client._on_disconnect = self._on_disconnect
        self._mqtt_client._on_message = self._on_message
        self._mqtt_client._on_subscribe = self._on_subscribe
        self._mqtt_client.connect(self.hostname, self._port, 60)
        self._mqtt_client.loop_start()

    def _get_registers_with(self, required_key):
        # Returns the registers containing the required_key
        return [register for register in self.registers if required_key in register]

    def poll(self):
        try:
            self._mb.poll()
        except Exception as e:
            logging.exception("Failed to poll modbus device, attempting to reconnect: {}".format(e))
            self.connect_modbus()
            return

        for register in self._get_registers_with('pub_topic'):
            try:
                value = self._mb.get_value(register.get('table', 'holding'), register['address'])
            except:
                logging.warning("Couldn't get value from register {} in table {}".format(register['address'], register.get('table', 'holding')))
                continue
            # Filter the value through the mask, if present.
            value &= register.get('mask', 0xFFFF)
            # Scale the value, if required.
            value *= register.get('scale', 1)
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
            retain = register.get('retain', False)
            self._mqtt_client.publish(self.prefix+register['pub_topic'], value, retain=retain)

    def _on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT.")
        else:
            logging.error("Couldn't connect to MQTT.")
            return
        # Subscribe to all the set topics.
        for register in self._get_registers_with('set_topic'):
            self._mqtt_client.subscribe(self.prefix+register['set_topic'])
            print("Subscribed to {}".format(self.prefix+register['set_topic']))
        self._mqtt_client.publish(self.prefix+'modbus4mqtt', 'hi')

    def _on_disconnect(self, client, userdata, flags, rc):
        logging.info("Disconnected")

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        pass

    def _on_message(self, client, userdata, msg):
        # print("got a message: {}: {}".format(msg.topic, msg.payload))
        topic = msg.topic[len(self.prefix):]
        for register in [register for register in self.registers if 'set_topic' in register]:
            if topic != register['set_topic']:
                continue
            # We received a set topic message for this topic.
            value = msg.payload
            if 'value_map' in register:
                try:
                    value = str(value, 'utf-8')
                    if not value in register['value_map']:
                        logging.warning("Value not in value_map. Topic: {}, value: {}, valid values: {}".format(topic, value, register['value_map'].keys()))
                        continue
                    # Map the value from the human-readable form into the raw modbus number
                    value = register['value_map'][value]
                except UnicodeDecodeError:
                    logging.warning("Failed to decode MQTT payload as UTF-8. Can't compare it to the value_map for register {}".format(register))
                    continue
            try:
                # Scale the value, if required.
                value = float(value)
                value = round(value/register.get('scale', 1))
            except ValueError:
                logging.error("Failed to convert register value for writing. Bad/missing value_map? Topic: {}, Value: {}".format(topic, value))
                continue
            self._mb.set_value(register.get('table', 'holding'), register['address'], int(value), register.get('mask', 0xFFFF))

    def _load_modbus_config(self, path):
        return yaml.load(open(path,'r').read(), Loader=yaml.FullLoader)

    def loop_forever(self):
        while True:
            # TODO this properly.
            self.poll()
            sleep(self.config['update_rate'])

@click.command()
@click.option('--hostname', default='localhost', help='The hostname or IP address of the MQTT server.')
@click.option('--port', default=1883, help='The hostname or IP address of the MQTT server.')
@click.option('--username', default='username', help='The hostname or IP address of the MQTT server.')
@click.option('--password', default='password', help='The hostname or IP address of the MQTT server.')
@click.option('--config', default='./Sungrow_SH5k_20.yaml', help='The YAML config file for your modbus device.')
@click.option('--mqtt_topic_prefix', default='modbus4mqtt', help='Prefixed to everything this publishes')
def main(hostname, port, username, password, config, mqtt_topic_prefix):
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S')
    i = mqtt_interface(hostname, port, username, password, config, mqtt_topic_prefix)
    i.connect()
    i.loop_forever()

if __name__ == '__main__':
    main()
