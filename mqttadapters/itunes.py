#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging
import threading
import paho.mqtt.client as mqtt
from argparse import ArgumentParser
import applescript
import time
from common import *
import Queue
import json
import sys

DEFAULT_TOPIC_BASE = 'itunes/'

topic_base = DEFAULT_TOPIC_BASE
logger = logging.getLogger()


script = applescript.AppleScript('''
on current_state()
    tell application "iTunes"
        try
            set c to container of current track
        on error
            return {state: "stopped"}
        end try
        set cinfo to {playlist_name:name of c}
        set tinfo to {track_name:name of current track, track_artist:artist of current track, track_album:album of current track}
        if player state is playing then
            set pinfo to {state: "playing"}
        end if
        if player state is paused then
            set pinfo to {state: "paused"}
        end if
        if player state is stopped then
            set pinfo to {state: "paused"}
        end if
        return pinfo & cinfo & tinfo
    end tell
end current_state

on pause_track()
    tell application "iTunes" to pause
end pause_track

on stop_track()
    tell application "iTunes" to stop
end stop_track

on play_track(track_name, track_artist, track_album, playlist_name)
    tell application "iTunes"
        set target_playlist to (user playlist playlist_name)
        set search_result to search target_playlist for track_name
        repeat with result_item in search_result
            if name of result_item is equal to track_name and artist of result_item is equal to track_artist and album of result_item is equal to track_album then
               play result_item
               return {track_name: name of result_item, track_artist: artist of result_item, track_album: album of result_item, user_playlist:name of target_playlist}
            end if
        end repeat
        return {}
    end tell
end play_track

on search_for_playlist(track_name, playlist_name)
    tell application "iTunes"
        set target_playlist to (user playlist playlist_name)
        set search_result to search target_playlist for track_name
        set infos to {}
        repeat with result_item in search_result
            if name of result_item contains track_name then
                set the end of infos to {track_name: name of result_item, track_artist: artist of result_item, track_album: album of result_item, playlist_name:name of target_playlist}
            end if
        end repeat
        return infos
    end tell
end search_for_playlist

on search_for_album(track_name, album_title)
    tell application "iTunes"
        set music_playlist to (get some playlist whose special kind is Music)
        set search_result to search music_playlist for album_title only albums
        set infos to {}
        repeat with result_item in search_result
            if album of result_item contains album_title and name of result_item contains track_name
                set the end of infos to {track_name: name of result_item, track_artist: artist of result_item, track_album: album of result_item, playlist_name:name of music_playlist}
            end if
        end repeat
        return infos
    end tell
end search_for_album

on search_for_artist(track_name, artist_name)
    tell application "iTunes"
        set music_playlist to (get some playlist whose special kind is Music)
        set search_result to search music_playlist for artist_name only artists
        set infos to {}
        repeat with result_item in search_result
            if artist of result_item contains artist_name and name of result_item contains track_name
                set the end of infos to {track_name: name of result_item, track_artist: artist of result_item, track_album: album of result_item, playlist_name:name of music_playlist}
            end if
        end repeat
        return infos
    end tell
end search_for_artist
''')


class LibraryBrowser(threading.Thread):

    def __init__(self, itunes_id, mqtt_client, interval=1.0):
        super(LibraryBrowser, self).__init__()
        self.mqtt_client = mqtt_client
        self.itunes_id = itunes_id
        self.interval = interval
        self.actions = Queue.Queue()
        self.lock = threading.Lock()
        self.in_service = True
        self.daemon = True

    def on_connect(self, client, userdata, flags, rc):
        logger.info('Connected rc=%d' % rc)
        client.subscribe(self._get_topic('current'))

    def on_message(self, client, userdata, msg):
        logger.info('Received: %s, %s' % (msg.topic, msg.payload))
        try:
            next_state = json.loads(msg.payload)
            if next_state['state'] == 'stopped' or next_state['state'] == 'paused':
                self.actions.put({'state': next_state['state']})
            elif next_state['state'] == 'playing':
                self._on_play(next_state)
        except:
            logger.error('Unexpected error: %s' % sys.exc_info()[0])


    def inactivate(self):
        with self.lock:
            self.in_service = False

    def run(self):
        last_state = None
        logger.debug('Register: {}'.format(self.itunes_id))
        self._on_added()
        try:
            next_action = None
            while(self._in_service()):
                state = script.call('current_state')
                try:
                    if next_action:
                        if next_action['state'] == 'playing':
                            if last_state != next_action:
                                last_state = next_action
                                logger.info('Play: {}'.format(next_action))
                                script.call('play_track', next_action['track_name'], next_action['track_artist'], next_action['track_album'], next_action['playlist_name'])
                            else:
                                logger.debug('Skipped: {}'.format(next_action))
                        elif last_state and last_state['state'] != next_action['state']:
                            logger.info('Apply: {}'.format(next_action))
                            if next_action['state'] == 'paused':
                                last_state['state'] = next_action['state']
                                script.call('pause_track')
                            elif next_action['state'] == 'stopped':
                                last_state = next_action
                                script.call('stop_track')
                        else:
                            logger.debug('Skipped: {}'.format(next_action))
                    elif not last_state or state != last_state:
                        logger.info('Changed: {}'.format(state))
                        self.mqtt_client.publish(self._get_topic('current'),
                                                 payload=json.dumps(state))
                        last_state = state
                except:
                    logger.error('Unexpected error: %s' % sys.exc_info()[0])
                try:
                    next_action = self.actions.get(True, self.interval)
                except Queue.Empty:
                    next_action = None
        finally:
            self._on_removed()

    def _in_service(self):
        with self.lock:
            return self.in_service

    def _get_topic(self, name=None):
        if name is None:
            return topic_base + self.itunes_id
        else:
            return topic_base + self.itunes_id + '/' + name

    def _on_added(self):
        host_info = {'status': 'added', 'itunes': self.itunes_id,
                     'topic': {'current': self._get_topic('current'),
                               'candidates': self._get_topic('candidates')}}
        self.mqtt_client.publish(self._get_topic(),
                                 payload=json.dumps(host_info))

    def _on_removed(self):
        host_info = {'status': 'removed', 'itunes': self.itunes_id,
                     'topic': {'current': self._get_topic('current'),
                               'candidates': self._get_topic('candidates')}}
        self.mqtt_client.publish(self._get_topic(),
                                 payload=json.dumps(host_info))

    def _on_play(self, track_info):
        if 'playlist_name' in track_info:
            results = script.call('search_for_playlist', track_info['track_name'], track_info['playlist_name'])
        elif 'track_album' in track_info:
            results = script.call('search_for_album', track_info['track_name'], track_info['track_album'])
        elif 'track_artist' in track_info:
            results = script.call('search_for_artist', track_info['track_name'], track_info['track_artist'])
        else:
            raise ValueError('Insufficient parameters: {}'.format(track_info))
        logger.info('Search result: {}'.format(results))
        self.mqtt_client.publish(self._get_topic('candidates'), payload=json.dumps(results))
        if len(results) == 1:
            self.actions.put(dict(results[0].items() + [('state', 'playing')]))


def main():
    desc = '%s [Args] [Options]\nDetailed options -h or --help' % __file__
    parser = ArgumentParser(description=desc)
    add_mqtt_arguments(parser, topic_default=DEFAULT_TOPIC_BASE)
    parser.add_argument('-i', '--id', type=str, dest='itunes_id', required=True,
                        help='ID for the iTunes')

    args = parser.parse_args()

    global topic_base
    topic_base = args.topic

    logging.basicConfig(level=get_log_level(args), format=LOG_FORMAT)

    mqtt_client = mqtt.Client()
    browser = LibraryBrowser(args.itunes_id, mqtt_client)
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
