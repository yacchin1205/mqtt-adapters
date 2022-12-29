#!/usr/bin/env python
# -*- coding: utf-8 -*-

import os
import time
import sys
import requests
import paho.mqtt.client as mqtt
import logging
import logging.config
import json
from argparse import ArgumentParser
from common import *

DEFAULT_TOPIC_BASE = 'nature/'

topic_base = DEFAULT_TOPIC_BASE
logger = logging.getLogger()

NATURE_API_URL = 'https://api.nature.global'


class NatureAppliance:
    def __init__(self, appliance):
        self.appliance = appliance

    def get_light_topic(self):
        if self.appliance['type'] != 'LIGHT':
            return None
        name = self.appliance['nickname']
        return get_topic(name) + '/light'

    def post(self, command):
        id = self.appliance['id']
        logger.info('Post: {} <- {}'.format(id, command))
        assert 'button' in command
        res = requests.post(
            '{}/1/appliances/{}/light?button={}'.format(
                NATURE_API_URL,
                id,
                command['button'],
            ),
            headers=_nature_request_headers(),
        )
        res.raise_for_status()

def get_topic(name):
    return topic_base + name.encode('utf8')

def _nature_request_headers():
    token = os.environ['NATURE_TOKEN']
    return {
        'accept': 'application/json',
        'authorization': 'Bearer {}'.format(token),
    }

def get_nature_appliances():
    res = requests.get(
        '{}/1/appliances'.format(NATURE_API_URL),
        headers=_nature_request_headers(),
    )
    res.raise_for_status()
    return [NatureAppliance(a) for a in res.json()]

def get_error_topic():
    return topic_base + 'error'

def nature_on_connect(client, userdata, flags, rc):
    logger.info('Connected rc=%d' % rc)
    client.subscribe(topic_base + '+/light')

def nature_on_message(client, userdata, msg):
    try:
        logger.info('Received: %s, %s' % (msg.topic, msg.payload))
        command = json.loads(msg.payload)
        assert(msg.topic.startswith(topic_base))
        topic_sub = msg.topic[len(topic_base):]
        assert(topic_sub.endswith('/light'))
        to = topic_sub[:-len('/light')]
        appliances = get_nature_appliances()
        if to == 'all':
            for host in appliances:
                host.post(command)
        else:
            for host in appliances:
                if host.get_light_topic() == msg.topic.encode('utf8'):
                    host.post(command)
    except (ValueError, IOError):
        logger.error('Unexpected error: %s' % sys.exc_info()[0])
        errorinfo = {'message': 'Error occurred: %s' % sys.exc_info()[0]}
        client.publish(get_error_topic(), payload=json.dumps(errorinfo))


def main():
    desc = '%s [Args] [Options]\nDetailed options -h or --help' % __file__
    parser = ArgumentParser(description=desc)
    add_mqtt_arguments(parser, topic_default=DEFAULT_TOPIC_BASE)

    args = parser.parse_args()

    global topic_base
    topic_base = args.topic

    logging.basicConfig(level=get_log_level(args), format=LOG_FORMAT)

    assert 'NATURE_TOKEN' in os.environ

    mqtt_client = mqtt.Client()
    mqtt_client.on_connect = nature_on_connect
    mqtt_client.on_message = nature_on_message
    connect_mqtt(args, mqtt_client)
    try:
        mqtt_client.loop_forever()
    except KeyboardInterrupt:
        pass

if __name__ == '__main__':
    main()
