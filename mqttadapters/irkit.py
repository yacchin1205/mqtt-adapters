#!/usr/bin/env python
# -*- coding: utf-8 -*-

from zeroconf import ServiceBrowser, Zeroconf
import threading
import time
import ipaddress
import subprocess
import sys
import requests
import paho.mqtt.client as mqtt
import logging
import logging.config
import json
from argparse import ArgumentParser
from common import *

SERVICE_TYPE = '_irkit._tcp.local.'
DEFAULT_TOPIC_BASE = 'irkit/'
CHECK_INTERVAL_SEC = 5.0
SERVICE_TIMEOUT = 60

topic_base = DEFAULT_TOPIC_BASE
logger = logging.getLogger()


def get_topic(name):
    if '.' in name:
        return topic_base + name[:name.index('.')].encode('utf8')
    else:
        return topic_base + name.encode('utf8')


def get_messages_topic(name):
    return get_topic(name) + '/messages'


def get_error_topic():
    return topic_base + 'error'


class HostListener(object):

    hosts = {}
    removed = []

    def __init__(self, mqtt_client):
        self.mqtt_client = mqtt_client
        self.finished_lock = threading.Lock()

    def remove_service(self, zeroconf, type, name):
        logger.info('Service %s removed' % (name,))
        self._refresh_hosts()
        if name in self.hosts:
            self.hosts[name].inactivate()

    def add_service(self, zeroconf, type, name):
        info = zeroconf.get_service_info(type, name)
        logger.info('Service %s added, service info: %s' % (name, info))
        self._refresh_hosts()
        if info:
            if name not in self.hosts:
                host = IRKitHost(name, info.address, info.port,
                                 self.mqtt_client)
                host.on_finished = self.on_finished
                self.hosts[name] = host
                host.start()
                logger.info('Subscribe: %s' % get_messages_topic(name))
            else:
                self.hosts[name].activate()

    def on_connect(self, client, userdata, flags, rc):
        logger.info('Connected rc=%d' % rc)
        client.subscribe(topic_base + '+/messages')

    def on_message(self, client, userdata, msg):
        try:
            logger.info('Received: %s, %s' % (msg.topic, msg.payload))
            command = json.loads(msg.payload)
            assert(msg.topic.startswith(topic_base))
            topic_sub = msg.topic[len(topic_base):]
            assert(topic_sub.endswith('/messages'))
            to = topic_sub[:-len('/messages')]
            if to == 'all':
                for host in self.hosts.values():
                    host.post(command)
            else:
                for name, host in self.hosts.items():
                    if get_messages_topic(name) == msg.topic:
                        host.post(command)
        except (ValueError, IOError):
            logger.error('Unexpected error: %s' % sys.exc_info()[0])
            errorinfo = {'message': 'Error occurred: %s' % sys.exc_info()[0]}
            client.publish(get_error_topic(), payload=json.dumps(errorinfo))

    def on_finished(self, name):
        logger.debug('Finished: %s' % name)
        with self.finished_lock:
            self.removed.append(name)

    def _refresh_hosts(self):
        with self.finished_lock:
            if len(self.removed) > 0:
                for name in self.removed:
                    logger.info('Removed: %s' % name)
                    del self.hosts[name]
                self.removed = []


class ReceivedQueue(object):

    items = []

    def __init__(self, size):
        self.lock = threading.Lock()
        self.size = size

    def put(self, item):
        with self.lock:
            self.items.append(item)
            if len(self.items) > self.size:
                del self.items[-1]

    def has(self, item):
        with self.lock:
            if item in self.items:
                found = self.items.index(item)
                del self.items[found]
                return True
            else:
                return False


class IRKitHost(threading.Thread):

    on_finished = None

    def __init__(self, name, address, port, mqtt_client):
        super(IRKitHost, self).__init__()
        self.name = name
        self.host = '%s:%d' % (str(ipaddress.ip_address(address)), port)
        self.mqtt_client = mqtt_client
        self.lock = threading.RLock()
        self.sem = threading.Semaphore()
        self.service_timeout = None
        self.daemon = True
        self.queue = ReceivedQueue(5)

    def inactivate(self):
        with self.lock:
            self.service_timeout = SERVICE_TIMEOUT

    def activate(self):
        with self.lock:
            self.service_timeout = None

    def post(self, messages):
        if self.queue.has(messages):
            logger.debug('Skipped: already received messages')
        else:
            logger.info('Sending "%s"' % str(messages))
            with self.sem:
                session = requests.Session()
                resp = session.post('http://%s/messages' % self.host,
                                    data=json.dumps(messages),
                                    headers={'X-Requested-With': 'homeui'},
                                    timeout=5.0)
                logger.debug("Response: %s (status_code=%d)" % (resp.content, resp.status_code))
                resp.raise_for_status()

    def _is_in_service(self):
        with self.lock:
            if self.service_timeout is None:
                return True
            self.service_timeout -= 1
            if self.service_timeout > 0:
                return True
            return False

    def run(self):
        host_info = {'status': 'added', 'name': self.name,
                     'topic': {'messages': get_messages_topic(self.name)}}
        self.mqtt_client.publish(get_topic(self.name),
                                 payload=json.dumps(host_info))

        while(self._is_in_service()):
            try:
                with self.sem:
                    session = requests.Session()
                    resp = session.get('http://%s/messages' % self.host,
                                       headers={'X-Requested-With': 'homeui'},
                                       timeout=3.0)
                logger.debug('GET "%s" from %s (status_code=%d)' % (resp.content, self.host, resp.status_code))
                resp.raise_for_status()
                if resp.content:
                    msg = resp.json()
                    topic = get_messages_topic(self.name)
                    self.queue.put(msg)
                    logger.info('Publishing... %s' % topic)
                    self.mqtt_client.publish(topic,
                                             payload=json.dumps(msg))
            except:
                logger.warning('Unexpected error: %s' % sys.exc_info()[0])
            time.sleep(CHECK_INTERVAL_SEC)

        if self.on_finished:
            self.on_finished(self.name)

        host_info['status'] = 'removed'
        self.mqtt_client.publish(get_topic(self.name),
                                 payload=json.dumps(host_info))


def main():
    desc = '%s [Args] [Options]\nDetailed options -h or --help' % __file__
    parser = ArgumentParser(description=desc)
    add_mqtt_arguments(parser, topic_default=DEFAULT_TOPIC_BASE)

    args = parser.parse_args()

    global topic_base
    topic_base = args.topic

    logging.basicConfig(level=get_log_level(args), format=LOG_FORMAT)

    zeroconf = Zeroconf()
    mqtt_client = mqtt.Client()
    listener = HostListener(mqtt_client)
    mqtt_client.on_connect = listener.on_connect
    mqtt_client.on_message = listener.on_message
    connect_mqtt(args, mqtt_client)
    browser = ServiceBrowser(zeroconf, SERVICE_TYPE, listener)
    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        pass
    finally:
        zeroconf.close()

if __name__ == '__main__':
    main()
