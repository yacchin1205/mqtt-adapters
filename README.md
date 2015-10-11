# MQTT Adapters for Smart home devices

Currently supporting Philips Hue(http://www.meethue.com/) and IRKit(http://getirkit.com/)...

# How to install

```
$ sudo pip install git+https://github.com/yacchin1205/mqtt-adapters.git
```

# How to use with...

## Philips Hue

Use `mqtt-hue` command with parameters about the MQTT broker which you can use, like below

```
mqtt-hue -H lite.mqtt.shiguredo.jp -p 1883 -u username -P password -t username/hue/
```

At first, it will say the error message *The link button has not been pressed in the last 30 seconds*.
If you get the error, press the link button of your Hue, and restart the process.

`mqtt-hue` discovers your Hues on the network automatically, you can monitor and change lights via topics (ex. `username/hue/#`).

## IRKit

Use `mqtt-irkit` command the same as Hue...

```
mqtt-irkit -H lite.mqtt.shiguredo.jp -p 1883 -u username -P password -t username/irkit/
```

`mqtt-irkit` discovers your IRKits on the network automatically, you can monitor and send IR commands via topics.

