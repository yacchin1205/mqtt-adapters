#!/usr/bin/env python
# -*- coding: utf-8 -*-

import threading
import time
import sys
import grovepi
import paho.mqtt.client as mqtt
import logging
import logging.config
import json
from argparse import ArgumentParser
from common import *
from socket import gethostname

DEFAULT_TOPIC_BASE = 'grovepi/'
CHECK_INTERVAL_SEC = 1.0
MAX_INTERVAL = 60 * 10

topic_base = DEFAULT_TOPIC_BASE
logger = logging.getLogger()

DEFAULT_LIGHT_SENSOR = 0
DEFAULT_ULTRASONIC_SENSOR = 4


def get_topic(name):
    if '.' in name:
        return topic_base + name[:name.index('.')].encode('utf8')
    else:
        return topic_base + name.encode('utf8')


class GrovePiHost(threading.Thread):

    def __init__(self, name, mqtt_client):
        super(GrovePiHost, self).__init__()
        self.name = name
        self.mqtt_client = mqtt_client
        self.closed = False
        self.started = False
        self.lock = threading.RLock()
        self.lastValue = None
        self.lastTime = None

    def on_connect(self, client, userdata, flags, rc):
        logger.info('Connected rc=%d' % rc)
        if not self.started:
            self.started = True
            self.start()

    def close(self):
        logger.info('Closing')
        with self.lock:
            self.closed = True

    def run(self):
        host_info = {'status': 'added', 'name': self.name,
                     'topic': {'light': self._get_topic()}}
        self.mqtt_client.publish(get_topic(self.name),
                                 payload=json.dumps(host_info))

        self._prepare()

        while(not self.closed):
            try:
                with self.lock:
                    if not self.closed:
                        msg = self._read_msg()
                        if msg is not None:
                            logger.info('Publish: {}'.format(msg))
                            topic = self._get_topic()
                            self.mqtt_client.publish(topic,
                                                     payload=json.dumps(msg))
            except:
                logger.warning('Unexpected error: %s' % sys.exc_info()[0])
            time.sleep(CHECK_INTERVAL_SEC)

        host_info['status'] = 'removed'
        self.mqtt_client.publish(get_topic(self.name),
                                 payload=json.dumps(host_info))
        logger.info('Closed')


class Sensors(object):

    def __init__(self, sensors):
        self.sensors = sensors

    def on_connect(self, client, userdata, flags, rc):
        for s in self.sensors:
            s.on_connect(client, userdata, flags, rc)

    def close(self):
        for s in self.sensors:
            s.close()


class LightSensor(GrovePiHost):

    def __init__(self, name, mqtt_client, light=DEFAULT_LIGHT_SENSOR):
        super(LightSensor, self).__init__(name, mqtt_client)
        self.light = light

    def _get_topic(self):
        return get_topic(self.name) + '/light'

    def _prepare(self):
        return grovepi.pinMode(self.light, "INPUT")

    def _read_msg(self):
        # Get sensor value
        sensor_value = grovepi.analogRead(self.light)

        # Calculate resistance of sensor in K
        resistance = (float)(1023 - sensor_value) * 10 / sensor_value

        msg = {'raw': sensor_value, 'resistance': resistance}
        logger.debug('Light: {}'.format(msg))
        if self.lastValue is not None and \
            self.lastTime is not None and \
            self.lastTime + MAX_INTERVAL > time.time() and \
            abs(self.lastValue - sensor_value) < 10:
            return None
        self.lastTime = time.time()
        self.lastValue = sensor_value
        return msg


class UltrasonicSensor(GrovePiHost):

    def __init__(self, name, mqtt_client, ultrasonic=DEFAULT_ULTRASONIC_SENSOR):
        super(UltrasonicSensor, self).__init__(name, mqtt_client)
        self.ultrasonic = ultrasonic

    def _get_topic(self):
        return get_topic(self.name) + '/ultrasonic'

    def _prepare(self):
        pass

    def _read_msg(self):
        # Get sensor value
        distant = grovepi.ultrasonicRead(self.ultrasonic)
        msg = {'distant': distant}
        logger.debug('Ultrasonic: {}'.format(msg))
        if self.lastValue is not None and \
            self.lastTime is not None and \
            self.lastTime + MAX_INTERVAL > time.time() and \
            abs(self.lastValue - distant) < 10:
            return None
        self.lastTime = time.time()
        self.lastValue = distant
        return msg


def main():
    desc = '%s [Args] [Options]\nDetailed options -h or --help' % __file__
    parser = ArgumentParser(description=desc)
    add_mqtt_arguments(parser, topic_default=DEFAULT_TOPIC_BASE)
    parser.add_argument('-l', '--light', type=str, dest='light',
                        default=DEFAULT_LIGHT_SENSOR,
                        help='Port number of Light Sensor(default: {})'
                             .format(DEFAULT_LIGHT_SENSOR))
    parser.add_argument('--ultrasonic', type=str, dest='ultrasonic',
                        default=DEFAULT_ULTRASONIC_SENSOR,
                        help='Port number of Ultrasonic Sensor(default: {})'
                             .format(DEFAULT_ULTRASONIC_SENSOR))

    args = parser.parse_args()

    global topic_base
    topic_base = args.topic

    logging.basicConfig(level=get_log_level(args), format=LOG_FORMAT)

    mqtt_client = mqtt.Client()
    light = LightSensor(gethostname(), mqtt_client, light=int(args.light))
    ultrasonic = UltrasonicSensor(gethostname(), mqtt_client,
                                  ultrasonic=int(args.ultrasonic))
    host = Sensors([light, ultrasonic])
    mqtt_client.on_connect = host.on_connect
    connect_mqtt(args, mqtt_client)
    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        host.close()

if __name__ == '__main__':
    main()
