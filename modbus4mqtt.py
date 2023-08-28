#!/usr/bin/python3

from time import sleep
import time
import json
import logging
from collections import defaultdict, OrderedDict
from ccorp.ruamel.yaml.include import YAML
import click
import paho.mqtt.client as mqtt

from . import modbus_interface
version = "EW.0.8"
MAX_DECIMAL_POINTS = 3
DEFAULT_SCAN_RATE_S = 5

def set_json_message_value(message, json_key, value):
  target = message
  json_keys = json_key.split('.')
  if len(json_keys) > 1:
    for json_key in json_keys[:-1]:
      if json_key not in target:
        target[json_key] = dict()
      target = target[json_key]

  old = target.get(json_keys[-1], None)
  if old is not None:
    if not isinstance(old, list):
      old = [ old ]
    old.append(value)
    value = old
  target[json_keys[-1]] = value

class mqtt_interface():
    config = { }
    def __init__(self, hostname, port, username, password, config_file, mqtt_topic_prefix,
                 use_tls=True, insecure=False, cafile=None, cert=None, key=None):
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
        self.modbus_connect_retries = -1  # Retry forever by default
        self.modbus_reconnect_sleep_interval = 5  # Wait this many seconds between modbus connection attempts
        
        self._errors = { }

    def get_DeviceUnit(self, register, unit=None):
      device = register.get('device', None)
      if device:
        return device
      table = register.get('table', 'holding')
      unit = register.get('unit', unit)
      if unit is None:
        unit = 0x03
        if 'options' in self.config:
          unit = self.config['options'].get('unit', unit)
      return modbus_interface.DeviceUnit(table=table, unit=unit)
    
    def connect(self):
        # Connects to modbus and MQTT.
        self.connect_modbus()
        self.connect_mqtt()

    def connect_modbus(self):
        self._mb = modbus_interface.modbus_interface(self.config['url'],
                                                     options=self.config.get('options', { }),
                                                    )
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
        logging.info("Connected to modbus on {}".format(self._mb.getDevice()))
        cnt = set()
        for register in self.registers:
          address = register.get('address', None)
          if address is not None:
            device_unit = self.get_DeviceUnit(register)
            key = (device_unit, address)
            self._mb.add_monitor_register(device_unit, address, register.get('type', 'uint16'))
            cnt.add( key )
          register['value'] = None
        unit_cnt = len(set(map(lambda x: x[0].unit, cnt)))
        logging.info("Added {} unique registers.".format(len(cnt)))
        if unit_cnt > 1:
          logging.info("Will poll {} units.".format(unit_cnt))
        self._mb.prepare()

    def modbus_connection_failed(self):
        exit(1)

    def connect_mqtt(self):
        self._mqtt_client = mqtt.Client()
        self._mqtt_client.username_pw_set(self.username, self.password)
        self._mqtt_client._on_connect = self._on_connect
        self._mqtt_client._on_disconnect = self._on_disconnect
        self._mqtt_client._on_message = self._on_message
        self._mqtt_client._on_subscribe = self._on_subscribe
        if self.use_tls:
            self._mqtt_client.tls_set(ca_certs=self.cafile, certfile=self.cert, keyfile=self.key)
            self._mqtt_client.tls_insecure_set(self.insecure)
        self._mqtt_client.connect(self.hostname, self._port, 60)
        self._mqtt_client.loop_start()

    def _get_registers_with(self, required_key):
        # Returns the registers containing the required_key
        return [register for register in self.registers if required_key in register]

    def getRegisterError(self, registerKey):
      return self._errors.get(registerKey, False)

    def _setRegisterError(self, registerKey):
      if registerKey in self._errors:
        return False
      self._errors[registerKey] = True
      return True

    def _clearRegisterError(self, registerKey):
      self._errors.pop(registerKey, None)

    def poll(self):
        try:
            self._mb.poll()
        except Exception as e:
            logging.exception("Failed to poll modbus device, attempting to reconnect: {}".format(e))
            self.connect_modbus()
            return

        # This is used to store values that are published as JSON messages rather than individual values
        json_messages = {}
        json_messages_retain = {}
        json_messages_changed = {}
        json_messages_sort = {}

        for register in self._get_registers_with('pub_topic'):
            deviceUnit = self.get_DeviceUnit(register)
            special = register.get('special', None)
            address = register.get('address', None)
            registerKey = (deviceUnit, special or address)
            
            if special:
              if special == 'epoch':
                value = int( time.time() )
              elif special == 'time':
                value = time.localtime()
                format = register.get('format', '%c')
                if format:
                  value = time.strftime(format, value)
              else:
                if self._setRegisterError(registerKey):
                  logging.warning("Register {}: Unknown special {}".format(deviceUnit, special))
                continue
            elif address is not None:
              try:
                  value = self._mb.get_value( deviceUnit,
                                              address,
                                              register.get('type', 'uint16'))
              except Exception as e:
                  if self._setRegisterError(registerKey):
                    logging.warning("Couldn't get value from register {}, address {}".format(deviceUnit, address))
                  logging.debug(e, stack_info=True)
                  continue
            
              # Filter the value through the mask, if present.
              if 'mask' in register:
                  # masks only make sense for uint
                  if register.get('type', 'uint16') in ['uint16', 'uint32', 'uint64']:
                      value &= register.get('mask')
              # Scale the value, if required.
              value *= register.get('scale', 1)
              # Clamp the number of decimal points
              value = round(value, register.get('precision', MAX_DECIMAL_POINTS))
            else:
              if self._setRegisterError(registerKey):
                logging.warning("Unsupported register type for register {}".format(registerKey))
              continue
            
            changed = not register.get('pub_only_on_change', True)
            if value != register['value']:
                if not special:
                  changed = True
                register['value'] = value

            # Map from the raw number back to the human-readable form
            if 'value_map' in register:
                if value in register['value_map'].values():
                    # This is a bit weird...
                    value = [human for human, raw in register['value_map'].items() if raw == value][0]

            self._clearRegisterError(registerKey)

            if register.get('json_key', False):
                # This value won't get published to MQTT immediately. It gets stored and sent at the end of the poll.
                if register['pub_topic'] not in json_messages:
                    json_messages[register['pub_topic']] = OrderedDict()
                    json_messages_retain[register['pub_topic']] = False
                    json_messages_sort[register['pub_topic']] = register['json_key']
                    json_messages_changed[register['pub_topic']] = changed
                set_json_message_value(json_messages[register['pub_topic']], register['json_key'], value)
                if changed and not register.get('json_ignore_changed', False):
                  json_messages_changed[register['pub_topic']] = True
                if 'retain' in register:
                    json_messages_retain[register['pub_topic']] = register['retain']
            elif changed:
                retain = register.get('retain', False)
                self._mqtt_client.publish(self.prefix+register['pub_topic'], value, retain=retain)

        # Transmit the queued JSON messages.
        for topic, message in json_messages.items():
          if json_messages_changed[topic]:
            m = json.dumps(message, sort_keys=json_messages_sort[topic])
            self._mqtt_client.publish(self.prefix+topic, m, retain=json_messages_retain[topic])

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
        # Publish info message with retain
        # self._mqtt_client.publish(self.prefix+'modbus4mqtt', 'modbus4mqtt v{} connected.'.format(version.version), retain=True)

    def _on_disconnect(self, client, userdata, rc):
        logging.warning("Disconnected from MQTT. Attempting to reconnect.")

    def _on_subscribe(self, client, userdata, mid, granted_qos):
        pass

    def _on_message(self, client, userdata, msg):
        # print("got a message: {}: {}".format(msg.topic, msg.payload))
        # TODO Handle json_key writes. https://github.com/tjhowse/modbus4mqtt/issues/23
        topic = msg.topic[len(self.prefix):]
        for register in self._get_registers_with('set_topic'):
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
            self._mb.set_value(self.get_DeviceUnit(register), register['address'], int(value),
                               register.get('mask', 0xFFFF), type)

    # This throws ValueError exceptions if the imported registers are invalid
    @staticmethod
    def _validate_registers(registers, duplicate_json_key):
        all_pub_topics = set()
        duplicate_pub_topics = set()
        # Key: shared pub_topics, value: list of json_keys
        duplicate_json_keys = {}
        # Key: shared pub_topics, value: set of retain values (true/false)
        retain_setting = {}
        valid_types = modbus_interface.valid_types

        # Look for duplicate pub_topics
        for register in registers:
            type = register.get('type', 'uint16')
            if type not in valid_types:
                raise ValueError("Bad YAML configuration. Register has invalid type '{}'.".format(type))
            if 'json_key' in register and 'set_topic' in register:
                raise ValueError("Bad YAML configuration. Register with set_topic '{}' has a json_key specified. "
                                 "This is invalid. See https://github.com/tjhowse/modbus4mqtt/issues/23 for details."
                                 .format(register['set_topic']))
            if 'pub_topic' in register:
                if register['pub_topic'] in all_pub_topics:
                    duplicate_pub_topics.add(register['pub_topic'])
                    duplicate_json_keys[register['pub_topic']] = []
                    retain_setting[register['pub_topic']] = set()
                all_pub_topics.add(register['pub_topic'])

        # Check that all registers with duplicate pub topics have json_keys
        for register in registers:
            if 'pub_topic' in register:
              if register['pub_topic'] in duplicate_pub_topics:
                if 'json_key' not in register:
                    raise ValueError("Bad YAML configuration. pub_topic '{}' duplicated across registers without "
                                     "json_key field. Registers that share a pub_topic must also have a unique "
                                     "json_key.".format(register['pub_topic']))
                if register['json_key'] in duplicate_json_keys[register['pub_topic']]:
                  if duplicate_json_key == 'error':
                    raise ValueError("Bad YAML configuration. pub_topic '{}' duplicated across registers with a "
                                     "duplicated json_key field. Registers that share a pub_topic must also have "
                                     "a unique json_key.".format(register['pub_topic']))
                  if duplicate_json_key == 'warn':
                    logging.warning("Bogus YAML configuration. pub_topic '{}' duplicated across registers with a "
                                    "duplicated json_key field. Registers that share a pub_topic should have "
                                    "a unique json_key. Use duplicate_json_key to configure behavior.".format(register['pub_topic']))
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
        if 'options' not in result:
          result['options'] = { 'unit': 0x01 }
        self.address_offset = result.get('address_offset', 0)
        if 'registers' in result:
          # make copies of the register, to materialize all yaml aliases in each register
          registers = [dict(register) for register in result['registers'] if 'pub_topic' in register or 'set_topic' in register]
          for register in registers:
            register['address'] += self.address_offset
        elif 'devices' in result:
          registers = list()
          for device in result['devices']:
            # make copies of the register, to materialize all yaml aliases in each register
            device_registers = [dict(register) for register in device['registers'] if 'pub_topic' in register or 'set_topic' in register]

            unit = device.get('unit', None)
            device_topic = device.get('pub_topic', '')
            set_topic = device.get('set_topic', device_topic) # Use device_topic as default for set_topic
            address_offset = device.get('address_offset', self.address_offset)
            duplicate_json_key = device.get('duplicate_json_key', 'warn')
            sort_json_keys = device.get('sort_json_keys', True)

            for register in device_registers:
              if unit is not None:
                register['unit'] = unit
              if 'json_key' in register:
                register['sort_json_keys'] = sort_json_keys
              if 'pub_topic' in register:
                register['pub_topic'] = '/'.join(filter(None, [device_topic, register['pub_topic']]))
              if 'set_topic' in register:
                register['set_topic'] = '/'.join(filter(None, [set_topic, register['set_topic']]))
              if 'address' in register:
                register['address'] += address_offset
              register['device'] = self.get_DeviceUnit(register, unit)
            mqtt_interface._validate_registers(device_registers, duplicate_json_key)
            registers += device_registers

        mqtt_interface._validate_registers(registers, 'ignore')
        self.registers = registers
        return result

    def loop_forever(self):
        while True:
            # TODO this properly.
            self.poll()
            sleep(self.config.get('update_rate', DEFAULT_SCAN_RATE_S))

    def singlerun(self):
            self.poll()
            sleep(10)

@click.command()
@click.option('--hostname', default='localhost',
              help='The hostname or IP address of the MQTT server.', show_default=True)
@click.option('--port', default=1883,
              help='The port of the MQTT server.', show_default=True)
@click.option('--username', default='username',
              help='The username to authenticate to the MQTT server.', show_default=True)
@click.option('--password', default='password',
              help='The password to authenticate to the MQTT server.', show_default=True)
@click.option('--mqtt_topic_prefix', default='wgw12/energy/pzem-004t',
              help='A prefix for published MQTT topics.', show_default=True)
@click.option('--config', default='./modbus4mqtt.yaml',
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
@click.option('--loop', default='True',
              help='use False if you want to disable looping with update_rate and only want to run run 1 poll.', show_default=True)

def main(hostname, port, username, password, config, mqtt_topic_prefix, use_tls, insecure, cafile, cert, key, loop):
    logging.basicConfig(
        format='%(asctime)s %(levelname)-8s %(message)s',
        level=logging.INFO,
        datefmt='%Y-%m-%d %H:%M:%S')
    logging.info("Starting modbus4mqtt {}".format(version))
    i = mqtt_interface(hostname, port, username, password, config, mqtt_topic_prefix,
                       use_tls, insecure, cafile, cert, key)
    i.connect()
    if loop == 'True':
       i.loop_forever()
    else:
       i.singlerun()

if __name__ == '__main__':
    main()
