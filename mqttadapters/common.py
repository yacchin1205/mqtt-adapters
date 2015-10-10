import logging

def add_mqtt_arguments(parser, topic_default):
    parser.add_argument('-H', '--host', type=str, dest='host',
                        default='localhost', help='hostname of MQTT')
    parser.add_argument('-p', '--port', type=int, dest='port', default=1883,
                        help='port of MQTT')
    parser.add_argument('-u', '--username', type=str, dest='username',
                        default=None, help='username for the broker')
    parser.add_argument('-P', '--password', type=str, dest='password',
                        default=None, help='password for the broker')
    parser.add_argument('-t', '--topic', type=str, dest='topic',
                        default=topic_default,
                        help='Base topic name(default: {})'.format(topic_default))
    parser.add_argument('-v', dest='log_debug', action='store_true', help='verbose mode(log level=debug)')
    parser.add_argument('-q', dest='log_warn', action='store_true', help='quiet mode(log level=warn)')
                        
def connect_mqtt(args, client):
    if args.username is not None:
        client.username_pw_set(args.username, args.password)
    client.connect(args.host, args.port)

def get_log_level(args):
    if args.log_debug:
        return logging.DEBUG
    elif args.log_warn:
        return logging.WARN
    else:
        return logging.INFO