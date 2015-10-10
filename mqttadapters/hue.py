#!/usr/bin/env python
# -*- coding: utf-8 -*-

import ssdp
from xml.etree import ElementTree as ET
import requests
from urlparse import urlparse
from phue import Bridge
import threading
import time
import logging
import logging.config
import paho.mqtt.client as mqtt
from argparse import ArgumentParser
import json
import Queue
from common import *

TOPIC_BASE = 'hue/'
ERROR_TOPIC = TOPIC_BASE + 'error'

namespaces = {'upnp': 'urn:schemas-upnp-org:device-1-0'}
logger = logging.getLogger()


def get_topic(udn):
    assert(udn.startswith('uuid:'))
    return TOPIC_BASE + udn[5:].encode('utf8')


def get_light_topic(udn, light_id):
    return '%s/light/%s' % (get_topic(udn), light_id)


class DeviceInfo(object):

    def __init__(self, xml):
        tree = ET.fromstring(xml)
        self.model_name = tree.find('upnp:device/upnp:modelName',
                                    namespaces)
        self.friendly_name = tree.find('upnp:device/upnp:friendlyName',
                                       namespaces)
        self.udn = tree.find('upnp:device/upnp:UDN', namespaces)
        self.urlbase = tree.find('upnp:URLBase', namespaces)
        if self.model_name is not None:
            self.model_name = self.model_name.text
        if self.friendly_name is not None:
            self.friendly_name = self.friendly_name.text
        if self.urlbase is not None:
            self.urlbase = self.urlbase.text
        if self.udn is not None:
            self.udn = self.udn.text

    def __repr__(self):
        return '<DeviceInfo({model_name}, {friendly_name}, {urlbase})>' \
               .format(**self.__dict__)

    def get_ip(self):
        o = urlparse(self.urlbase)
        if ':' in o.netloc:
            return o.netloc.split(':')[0]
        else:
            return o.netloc


class DeviceBrowser(threading.Thread):

    devices = {}

    def __init__(self, mqtt_client, interval=10.0):
        super(DeviceBrowser, self).__init__()
        self.mqtt_client = mqtt_client
        self.interval = interval
        self.lock = threading.Lock()
        self.in_service = True
        self.daemon = True

    def on_connect(self, client, userdata, flags, rc):
        logger.info('Connected rc=%d' % rc)
        client.subscribe(TOPIC_BASE + '+/light/+/status')

    def on_message(self, client, userdata, msg):
        logger.info('Received: %s, %s' % (msg.topic, msg.payload))
        try:
            topic = msg.topic.split('/')
            status = json.loads(msg.payload)
            light_id = topic[3]
            for dev in self.devices.values():
                if msg.topic.startswith(dev['topic']):
                    dev['bridge'].change(light_id, status)
        except (ValueError):
            logger.error('Unexpected error: %s' % sys.exc_info()[0])
            errorinfo = {'message': 'Error occurred: %s' % sys.exc_info()[0]}
            client.publish(ERROR_TOPIC, payload=json.dumps(errorinfo))

    def inactivate(self):
        with self.lock:
            self.in_service = False

    def run(self):
        while(self._in_service()):
            devices = self._discover_hue()
            logger.debug('Found: %s' % str(devices))
            added = []
            removed = []
            for dev in devices:
                if dev.udn not in self.devices:
                    added.append(dev)
                else:
                    self.devices[dev.udn]['remove'] = 0
            for dev in self.devices.values():
                found = filter(lambda x: x.udn == dev['device'].udn, devices)
                if not found:
                    dev['remove'] += 1
                    logger.debug('Not found: %s (count=%d)'
                                 % (dev['device'].udn, dev['remove']))
                    if dev['remove'] > 5:
                        removed.append(dev['device'])
            for d in added:
                self.on_added(d)
                b = HueBridge(self.mqtt_client, d)
                self.devices[d.udn] = {'remove': 0, 'device': d, 'bridge': b,
                                       'topic': get_topic(d.udn)}
                b.start()
            for d in removed:
                self.on_removed(d)
                self.devices[d.udn]['bridge'].inactivate()
                del self.devices[d.udn]
            time.sleep(self.interval)

    def on_added(self, device):
        logger.info('Added: %s' % device.urlbase)
        host_info = {'status': 'added', 'urlbase': device.urlbase,
                     'udn': device.udn}
        self.mqtt_client.publish(get_topic(device.udn),
                                 payload=json.dumps(host_info))

    def on_removed(self, device):
        logger.info('Removed: %s' % device.urlbase)
        host_info = {'status': 'removed', 'urlbase': device.urlbase,
                     'udn': device.udn}
        self.mqtt_client.publish(get_topic(device.udn),
                                 payload=json.dumps(host_info))

    def _in_service(self):
        with self.lock:
            return self.in_service

    def _discover_hue(self):
        responses = ssdp.discover('ssdp:discover')
        urn_device = 'urn:schemas-upnp-org:device:basic:1'
        targets = filter(lambda x: x.st == urn_device,
                         responses)
        devices = []
        for target in targets:
            resp = requests.get(target.location)
            dev = DeviceInfo(resp.content)
            if dev.model_name.startswith('Philips hue bridge'):
                devices.append(dev)
        return devices


class HueBridge(threading.Thread):

    def __init__(self, mqtt_client, device, interval=1.0):
        super(HueBridge, self).__init__()
        self.mqtt_client = mqtt_client
        self.device = device
        self.interval = interval
        self.actions = Queue.Queue()
        self.lock = threading.Lock()
        self.in_service = True

    def inactivate(self):
        with self.lock:
            self.in_service = False

    def change(self, light_id, status):
        logger.info('Reserved: %s, %s' % (self.device.udn, light_id))
        self.actions.put({'id': light_id, 'status': status})

    def run(self):
        b = Bridge(self.device.get_ip())
        b.connect()
        logger.info('Bridge state: %s' % str(b.get_api()))
        lights = {}
        next_action = None
        while(self._in_service()):
            if next_action:
                logger.debug('Changing... %s' % str(next_action))
                light_id = next_action['id']
                next_status = next_action['status']
                if light_id in lights \
                   and lights[light_id]['last_status'] != next_status:
                    logger.info('Change: %s, %s' % (light_id, next_status))
                    light = lights[light_id]['device']
                    if 'hue' in next_status:
                        light.hue = next_status['hue']
                    if 'saturation' in next_status:
                        light.saturation = next_status['saturation']
                    if 'brightness' in next_status:
                        light.brightness = next_status['brightness']
                    if 'on' in next_status:
                        light.on = next_status['on']
            current = {}
            added = []
            removed = []
            for l in b.lights:
                current[b.get_light_id_by_name(l.name)] = l
            for lid, light in current.items():
                if lid not in lights:
                    added.append(lid)
            for lid, light in lights.items():
                if lid not in current:
                    removed.append(lid)
            for lid in added:
                lights[lid] = {'device': current[lid], 'last_status': None}
                msg = {'id': lid, 'action': 'added',
                       'name': current[lid].name}
                self.mqtt_client.publish(get_light_topic(self.device.udn, lid),
                                         payload=json.dumps(msg))
            for lid in removed:
                old = lights[lid]['device']
                del lights[lid]
                msg = {'id': lid, 'action': 'removed', 'name': old.name}
                self.mqtt_client.publish(get_light_topic(self.device.udn, lid),
                                         payload=json.dumps(msg))
            for lid, light_entry in lights.items():
                light = light_entry['device']
                status = {'on': light.on, 'saturation': light.saturation,
                          'hue': light.hue, 'brightness': light.brightness}
                if status != light_entry['last_status']:
                    logger.debug('%s: status=%s' % (light.name, str(status)))
                    light_entry['last_status'] = status
                    topic = get_light_topic(self.device.udn, lid) + '/status'
                    self.mqtt_client.publish(topic, payload=json.dumps(status))
            try:
                next_action = self.actions.get(True, self.interval)
            except Queue.Empty:
                next_action = None

    def _in_service(self):
        with self.lock:
            return self.in_service


def main():
    desc = '%s [Args] [Options]\nDetailed options -h or --help' % __file__
    parser = ArgumentParser(description=desc)
    add_mqtt_arguments(parser)
    parser.add_argument('-l', '--logging', type=str, dest='logging',
                        default=None, help='path for logging.conf')

    args = parser.parse_args()

    if args.logging:
        logging.config.fileConfig(args.logging)
    else:
        logging.basicConfig()

    mqtt_client = mqtt.Client()
    browser = DeviceBrowser(mqtt_client)
    mqtt_client.on_connect = browser.on_connect
    mqtt_client.on_message = browser.on_message
    connect_mqtt(args, mqtt_client)

    browser.start()

    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        browser.inactivate()

if __name__ == '__main__':
    main()