
"""

Home automation extention for Magic Mirror only.

Imported conditionally if HAN host is 'magicmirror'

Adds magicmirror-specific threads to han.py
Provides a communication channel to the browser-based mirror Javascript
via a CORS-aware HTTP server.

1. Logs temp and humidity from Davis Hidden Valley weather station
2. Tracks health of all HAN devices
3. Serves HTTP requests from magicmirror Javascript app

"""

from http.server import BaseHTTPRequestHandler, HTTPServer
from json import dumps

import threading
import logging
import requests
import time


JAVASCRIPT_HTTP_PORT = 6446

# absolute paths to log files
LOG_PATH_BASE   = "/home/pi/home_automation/server/logs/"
MIRROR_LOG      = LOG_PATH_BASE + "mirror_log.txt"
DAVIS_LOG       = LOG_PATH_BASE + "davis_log.txt"
ECOBEE_LOG      = LOG_PATH_BASE + "ecobee_log.txt"
NODE_STATUS_LOG = LOG_PATH_BASE + "node_status_log.txt"

g_weather_latest = {}                 # global variable containing latest davis weather
g_weather_lock   = threading.Lock()


# CORS-aware HTTP request handler
# from https://royportas.com/posts/2019-03-02-cors-python/
class RequestHandler(BaseHTTPRequestHandler):

  def _send_cors_headers(self):
      """ Sets headers required for CORS """
      self.send_header("Access-Control-Allow-Origin", "*")
      self.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
      self.send_header("Access-Control-Allow-Headers", "x-api-key,Content-Type")

  def send_dict_response(self, d):
      """ Sends a dictionary (JSON) back to the client """
      self.wfile.write(bytes(dumps(d), "utf8"))

  def do_OPTIONS(self):
      self.send_response(200)
      self._send_cors_headers()
      self.end_headers()

  def do_GET(self):
      global g_weather_latest
      global g_weather_lock

      self.send_response(200)
      self._send_cors_headers()
      self.end_headers()

      # fetch global variable with latest davis weather sample
      with g_weather_lock:
          response = g_weather_latest
      response["status"] = "OK"
      self.send_dict_response(response)

  def do_POST(self):
      self.send_response(200)
      self._send_cors_headers()
      self.send_header("Content-Type", "application/json")
      self.end_headers()

      dataLength = int(self.headers["Content-Length"])
      data = self.rfile.read(dataLength)

      print(data)

      response = {}
      response["status"] = "OK"
      self.send_dict_response(response)


class httpServerThread(threading.Thread):
    def __init__(self, host_ip):
        threading.Thread.__init__(self)
        self.daemon = True
        self.host_ip = host_ip

    def run(self):
        mirror_log.info("Starting CORS-aware http server on port %s", JAVASCRIPT_HTTP_PORT)
        httpd = HTTPServer((self.host_ip, JAVASCRIPT_HTTP_PORT), RequestHandler)
        httpd.serve_forever()


class davisThread(threading.Thread):
    SAMPLE_INTERVAL = 10  # get weather every 10 mins, interval must divide fully into 60 (i.e. 10, 15, 20, not 11)
    DAVIS_URL = "http://192.168.1.230/v1/current_conditions"

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        global g_weather_latest
        global g_weather_lock

        mirror_log.info("davisThread running")

        while True:
            # fetch and log JSON report from Davis Weatherlink
            report = { }
            weather = requests.get(self.DAVIS_URL).json()
            report['o_temp'] = weather['data']['conditions'][0]['temp']
            report['o_hum']  = weather['data']['conditions'][0]['hum']
            report['wind_speed_1_min']  = weather['data']['conditions'][0]['wind_speed_avg_last_1_min']
            report['wind_dir_1_min']  = weather['data']['conditions'][0]['wind_dir_scalar_avg_last_1_min']
            report['wind_gust_10_min']  = weather['data']['conditions'][0]['wind_speed_hi_last_10_min']
            report['i_temp'] = weather['data']['conditions'][1]['temp_in']
            report['i_hum']  = weather['data']['conditions'][1]['hum_in']

            # update global variable with latest sample
            with g_weather_lock:
                g_weather_latest = report

            davis_log.info(report)

            # repeat ON every SAMPLE_INTERVAL mins
            time.sleep(60 * (self.SAMPLE_INTERVAL - (time.localtime().tm_min % self.SAMPLE_INTERVAL)))


class ecobeeThread(threading.Thread):
    SAMPLE_INTERVAL = 10  # get data every 10 mins, interval must divide fully into 60 (i.e. 15, 20, not 11)
    ECOBEE_URL = "http://ecobee.com"

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        mirror_log.info("ecobeeThread running")

        while True:
            # fetch JSON report from Ecobee
            # data = json.loads(os.system("curl" + ECOBEE_URL))
            # ecobee_log.info(data)

            # repeat ON every SAMPLE_INTERVAL mins
            time.sleep(60 * (self.SAMPLE_INTERVAL - (time.localtime().tm_min % self.SAMPLE_INTERVAL)))


def nodeStatusHandler(msg):
    # msg format: { 'device' : host_name }
    node_status_log.info(msg['host'])



host_name = 'magicmirror'       # han.py conditionally loads this module for magicmirror only
node_type = 'magicmirror'

# log configurations
log_datefmt = '%m/%d/%Y %H:%M:%S '
log_format ='%(asctime)s ' + host_name + ' %(levelname)s %(message)s'
log_formatter = logging.Formatter(fmt=log_format, datefmt=log_datefmt)
data_log_format ='%(asctime)s %(message)s'
data_log_formatter = logging.Formatter(fmt=data_log_format, datefmt=log_datefmt)

mirror_log_fh      = logging.handlers.RotatingFileHandler(MIRROR_LOG, maxBytes=(256*1024), backupCount=3)
davis_log_fh       = logging.handlers.RotatingFileHandler(DAVIS_LOG, maxBytes=(256*1024), backupCount=3)
ecobee_log_fh      = logging.handlers.RotatingFileHandler(ECOBEE_LOG, maxBytes=(256*1024), backupCount=3)
node_status_log_fh = logging.handlers.RotatingFileHandler(NODE_STATUS_LOG, maxBytes=(256*1024), backupCount=3)

mirror_log_fh.setLevel('INFO')
mirror_log_fh.setFormatter(log_formatter)
node_status_log_fh.setLevel('INFO')
node_status_log_fh.setFormatter(log_formatter)
davis_log_fh.setLevel('INFO')
davis_log_fh.setFormatter(data_log_formatter)
ecobee_log_fh.setLevel('INFO')
ecobee_log_fh.setFormatter(data_log_formatter)

mirror_log = logging.getLogger('han.mirror')
mirror_log.addHandler(mirror_log_fh)
davis_log       = logging.getLogger('han.mirror.davis')
davis_log.addHandler(davis_log_fh)
ecobee_log      = logging.getLogger('han.mirror.ecobee')
ecobee_log.addHandler(ecobee_log_fh)
node_status_log = logging.getLogger('han.mirror.node_status')
node_status_log.addHandler(node_status_log_fh)

mirror_log.info("")
mirror_log.info("MAGICMIRROR STARTING...")

# start threads
davis_t = davisThread()
davis_t.start()

ecobee_t = ecobeeThread()
ecobee_t.start()

host_ip = "192.168.1.200"
http_server_t = httpServerThread(host_ip)
http_server_t.start()
