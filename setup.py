#!/usr/bin/env python

from setuptools import setup

setup(name='MQTTAdapter',
      version='0.1',
      description='MQTT Adapters for Smart home devices',
      author='Satoshi Yazawa',
      author_email='yazawa@yzwlab.net',
      url='https://github.com/yacchin1205/mqtt-adapters',
      packages=['mqttadapters'],
      install_requires=['paho-mqtt', 'zeroconf', 'ipaddress', 'requests',
                        'phue', 'py-applescript'],
      entry_points={'console_scripts':
                    ['mqtt-irkit=mqttadapters.irkit:main',
                     'mqtt-hue=mqttadapters.hue:main',
                     'mqtt-itunes=mqttadapters.itunes:main']},
      )
