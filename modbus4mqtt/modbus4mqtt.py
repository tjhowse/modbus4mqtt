#!/usr/bin/python3

from time import sleep
import logging
import yaml
import click
import paho.mqtt.client as mqtt
from modbus_interface import modbus_interface

class mqtt_interface():
    def __init__(self, hostname, port, username, password, config_file, mqtt_topic_prefix):
        self.hostname = hostname
        self.port = port
        self.username = username
        self.password = password
        self.config = self.load_modbus_config(config_file)
        if not mqtt_topic_prefix.endswith('/'):
            mqtt_topic_prefix = mqtt_topic_prefix + '/'
        self.prefix = mqtt_topic_prefix
        # For ease of reference.
        self.registers = self.config['registers']

    def connect(self):
        # Connects to modbus and MQTT.
        self.mb = modbus_interface(self.config['ip'], self.config['port'], self.config['update_rate'])
        self.mb.connect()
        # Tells the modbus interface about the registers we consider interesting.
        for register in self.registers:
            self.mb.add_monitor_register(register['table'], register['address'])
            register['value'] = None

        self.client = mqtt.Client()
        self.client.username_pw_set(self.username, self.password)
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        self.client.connect(self.hostname, self.port, 60)
        self.client.loop_start()

    def get_registers_with(self, required_key):
        # Returns the registers containing the required_key
        return [register for register in self.registers if required_key in register]

    def poll(self):
        self.mb.poll()
        self.client.publish(self.prefix+'modbus4mqtt', 'poll')
        for register in self.get_registers_with('pub_topic'):
            value = self.mb.get_value(register['table'], register['address'])
            changed = False
            if value != register['value']:
                changed = True
                register['value'] = value
            if not changed and register.get('pub_only_on_change', False):
                continue
            # Map from the raw number back to the human-readable form
            if 'value_map' in register:
                if value in register['value_map'].values():
                    # This is a bit weird...
                    value = [human for human, raw in register['value_map'].items() if raw == value][0]
            retain = register.get('retain', False)
            self.client.publish(self.prefix+register['pub_topic'], value, retain=retain)

    def on_connect(self, client, userdata, flags, rc):
        if rc == 0:
            logging.info("Connected to MQTT.")
        else:
            logging.error("Couldn't connect to MQTT.")
        # Subscribe to all the set topics.
        for register in self.get_registers_with('set_topic'):
            self.client.subscribe(self.prefix+register['set_topic'])
            print("Subscribed to {}".format(self.prefix+register['set_topic']))
        self.client.publish(self.prefix+'modbus4mqtt', 'hi')

    def on_disconnect(self, client, userdata, flags, rc):
        print("Disconnected")

    def on_subscribe(self, client, userdata, mid, granted_qos):
        pass

    def on_message(self, client, userdata, msg):
        # print("got a message:")
        print("got a message: {}: {}".format(msg.topic, msg.payload))
        topic = msg.topic[len(self.prefix):]
        for register in [register for register in self.registers if 'set_topic' in register]:
            if topic != register['set_topic']:
                continue
            # We received a set topic message for this topic.
            value = str(msg.payload, 'utf-8')
            if 'value_map' in register:
                if not value in register['value_map']:
                    logging.warning("Value not in value_map. Topic: {}, value: {}, valid values: {}".format(topic, value, register['value_map'].keys()))
                    continue
                # Map the value from the human-readable form into the raw modbus number
                value = register['value_map'][value]
            self.mb.set_value(register['table'], register['address'], int(value))

    def load_modbus_config(self, path):
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
    i = mqtt_interface(hostname, port, username, password, config, mqtt_topic_prefix)
    i.connect()
    # i.poll()
    i.loop_forever()

if __name__ == '__main__':
    main()



