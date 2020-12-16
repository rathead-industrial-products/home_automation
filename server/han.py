
#

"""

Universal code base for Raspberry Pi based home automation nodes.

1. Fencepost Lighting Controller
2. Hunter Flow Meter Monitor
3. I2S DAC and Power Amplifier
4. Voltage/Current Sampler
5. TCP/IP Socket Server

The nodes have a Pi Bonnet that contains a power supply, an RS-422
driver for daisy-chaining LED strings, an I2S DAC and power amp to
drive a speaker, and an I2C ADC that monitors the supply voltage
and the load current (at 5V) of the node.

The Flow meter repurposes the LED driver GPIO to interface to the
reed relay interruptor in the flow sensor. It also monitors the
control lines to the solenoids and can detect when a zone is active.

"""

import threading
import queue
import time
import os
import socket
import pickle
import logging
import logging.handlers
import board
import busio
import digitalio
import adafruit_bus_device.spi_device
import random
import fencepost_neopixel_driver as npdrvr

SYSTEM_HOSTS = ("magicmirror", "flowmeter", "lidar", "fencepost-back-1", "fencepost-back-2", "fencepost-front-1")
HOME_AUTOMATION_PORT = 6445

# log files running as a linux service require an absolute path
LOG_PATH_BASE = "/home/pi/home_automation/server/logs/"
MASTER_LOG    = LOG_PATH_BASE + "master_log.txt"      # messages from all loggers
SERVER_LOG    = LOG_PATH_BASE + "server_log.txt"
FLOW_LOG      = LOG_PATH_BASE + "flow_log.txt"
VI_LOG        = LOG_PATH_BASE + "vi_log.txt"

lighting_cmd_q = queue.Queue()          # unbounded, but will empty as soon as a record is added
vi_q           = queue.Queue(10000)     # a week's worth of samples at 1 sample/min

g_vi_latest    = (0, 0)                 # global variable containing latest (v, i) sample
g_vi_lock      = threading.Lock()

g_flow_latest  = (1, 2)                 # global variable containing latest (gpm, gal) sample
g_active_zone  = "Off"                  # global variable containing currently active (ON) sprinkler zone
g_flow_lock    = threading.Lock()

# message types and supporting node types
MSG_TYPES = { 'DISPLAY'      : ('fencepost', ),
              'VI_QUERY'     : ('flowmeter', 'fencepost'),
              'VI_HISTORY'   : ('flowmeter', 'fencepost'),
              'FLOW_QUERY'   : ('flowmeter', ),
              'FLOW_HISTORY' : ('flowmeter', ),
              'PLAY_AUDIO'   : ('fencepost', ),
              'HEALTH_NOTICE': ('magicmirror', ), }

class audioThread(threading.Thread):
    #
    # amixer controls the volume
    #   > amixer
    #       Simple mixer control 'PCM',0
    #       Capabilities: volume
    #       Playback channels: Front Left - Front Right
    #       Capture channels: Front Left - Front Right
    #       Limits: 0 - 255
    #       Front Left: 128 [50%]
    #       Front Right: 128 [50%]
    #   > amixer controls
    #       numid=1,iface=MIXER,name='PCM'
    #   > amixer cget numid=1
    #       numid=1,iface=MIXER,name='PCM'
    #       ; type=INTEGER,access=rw---RW-,values=2,min=0,max=255,step=0
    #       : values=128,128
    #       | dBscale-min=-51.00dB,step=0.20dB,mute=0
    #   > amixer cset numid=1 256,256
    #   > amixer cset numid=1 50%,50%
    #
    # alsa is the audio driver
    #
    # omxplayer for .mp3 files
    #   omxplayer -o alsa audio/filename.mp3 > /dev/null
    #
    # aplay for .wav files
    #   aplay audio/filename.wav
    #

    SAMPLE_INTERVAL = 10.0     # sample every second

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        log.info("audioThread running")

        # Set up


        while True:
            os.system("omxplayer -o alsa --no-keys audio/intruder_alert.mp3 > /dev/null 2>&1 &")
            time.sleep(audioThread.SAMPLE_INTERVAL)


class viThread(threading.Thread):
    SAMPLE_INTERVAL = 60    # sample voltage and current once every minute
    READ_VIN = 0xD0
    READ_CUR = 0xF0

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        global g_vi_latest
        global g_vi_lock

        server_log.info("viThread running")

        # Set up SPI communications
        cs = digitalio.DigitalInOut(board.D22)      # NC, ignored. SPI_CS0 is used
        comm_port = busio.SPI(board.SCK, MOSI=board.MOSI, MISO=board.MISO)
        device = adafruit_bus_device.spi_device.SPIDevice(comm_port, cs)

        command = bytearray(3)
        result  = bytearray(3)

        while True:
            with device as spi:
                command[0] = viThread.READ_VIN
                command[1] = 0x00
                command[2] = 0x00
                spi.write_readinto(command, result)

            adc_value = int.from_bytes(result, byteorder='big')>>7 # bits 8-19 are valid
            vin = (33 * adc_value) / 4096     # adc input is Vin/10

            with device as spi:
                command[0] = viThread.READ_CUR
                command[1] = 0x00
                command[2] = 0x00
                spi.write_readinto(command, result)

            adc_value = int.from_bytes(result, byteorder='big')>>7 # bits 8-19 are valid
            cur = (1000 * adc_value) / 4096      # adc input is 3.3V @ 1000 mA of current

            if vi_q.full(): # remove oldest item if queue full
                try:
                    vi_q.get_nowait()
                except:
                    pass    # ignore if something else emptied queue first

            try:
                vi_q.put_nowait((vin, cur))
            except:
                server_log.error("Unable to add record to vi_q")

            # update global variable with latest sample
            with g_vi_lock:
                g_vi_latest = (vin, cur)

            # add to log file
            record = time.strftime("%m/%d/%Y %H:%M")+"\t%.1f"%vin+"\t%d"%cur
            vi_log.info(record)

            # report health to mindmentum.com
            os.system('curl -X POST -H "Content-Type: application/json" \
            -d \'{"device": "flowmeter"}\' \
            http://mindmentum.com/cgi-bin/ha_health.py > /dev/null 2>&1')

            time.sleep(viThread.SAMPLE_INTERVAL)


class flowThread(threading.Thread):
    #
    #   Hunter HC Pulse Flow Meter
    #   Flow meter interface is two wires (blue and white)
    #   connected through a reed relay.
    #   The relay pulses every 0.1 gallon.
    #   The duty cycle was empirically determined to be 60/40 open/closed.
    #   Max pulse rate vs flow rate:
    #   FLOW RATE (GPM)     6      12      18      24
    #   Pulses per second   1       2       3       4
    #   Minimum relay closed time at 24 GPM = 0.4 x 1/4 second = 100 ms
    #   The blue wire is connected to GPIO input pin D18 with an internal pullup.
    #   The white wire is connected to GND.
    #   The input is sampled every 100 ms. On a low to high transition
    #   the totalizer is incremented 0.1 gallons.
    #
    #   Flow rate is only computed when a pulse is received. If the flow substantially
    #   reduces or stops the igpm value will be incorrect. To compensate a ceiling
    #   value is substituted for the last computed igpm when there are no pulses,
    #   and a ceiling of < 0.5 gpm is rounded down to zero.
    #
    #   A logfile records cumulative gallons every minute when water is flowing.
    #   Each record is a line in the file of format <timestamp> cumgal.x gpm.x
    #
    #
    #   The power line to each solenoid from the sprinkler controller is monitored
    #   in order to sense when a sprinkler zone is active.
    #
    #   The pump power is active whenever any zone is active.
    #
    #   The LED is flashed at a proportional rate to water flow, or slowly if
    #   there is no flow.
    #

    SAMPLE_INTERVAL = 0.050     # sample flow pulse every 50 ms
    MIN_FLOW_RATE   = 0.35      # gpm, flow rates below this are rounded to zero
    LEAK_DETECT_DT  = 300       # seconds between pulses indicates possible leak

    # sprinker zones mapped to GPIO
    ZONE_MAP = { "led"      : board.D27,
                 "flow_sns" : board.D4,
                 "pump"     : board.D17,
                 "zone_1"   : board.D22,
                 "zone_2"   : board.D23,
                 "zone_3"   : board.D24,
                 "zone_4"   : board.D25,
                 "zone_5"   : board.D5,
                 "zone_6"   : board.D12,
                 "zone_7"   : board.D6,
                 "zone_8"   : board.D13,
                 "zone_9"   : board.D16,
                 "zone_10"  : board.D26,
                 "zone_11"  : board.D20 }

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        global g_flow_latest
        global g_flow_lock
        global g_active_zone

        server_log.info("flowThread running")

        # iniitalize gpio, replacing gpio number with pin object
        for zone in flowThread.ZONE_MAP.keys():
            io = digitalio.DigitalInOut(flowThread.ZONE_MAP[zone]) # create pin object
            flowThread.ZONE_MAP[zone] = io # save in dict in place of io number
            if zone is "led":
                io.direction = digitalio.Direction.OUTPUT
            else:
                io.direction = digitalio.Direction.INPUT

        gallons    = 0.0
        igpm       = 0.0
        last_state = False
        last_pulse = 0
        last_record_time = 0
        flowing    = False         # flowmeter activity detected

        while True:
            # determine instantaneous gpm assuming a pulse has been received
            # this is the ceiling of the current flow rate
            now = time.monotonic()
            if last_pulse == 0:                 # avoid bogus value at startup
                dt = flowThread.LEAK_DETECT_DT - 1
            else:
                dt = now - last_pulse
            igpm = 60 * (0.1/(dt)) # instantaneous GPM

            # very low flow rates are unrealistic, it is either a computed
            # ceiling or a leak
            if igpm < flowThread.MIN_FLOW_RATE:
                igpm = 0

            #
            # Pulse received, so computed igpm (ceiling) is actual igpm
            #
            current_state = flowThread.ZONE_MAP["flow_sns"].value  # is pulse line high or low
            if (not last_state) and current_state:      # on rising edge of pulse
                flowing = True                          # flowmeter activity detected
                gallons += 0.1                          # increment totalizer
                last_pulse = now                        # save to determine next interval
                with g_flow_lock: g_flow_latest = (igpm, gallons)
            else:
                if g_flow_latest[0] < igpm:    # record lesser of last sample or ceiling
                    igpm = g_flow_latest[0]
                with g_flow_lock: g_flow_latest = (igpm, gallons)

            # pulse the LED proportionally to the flow rate sensor
            # with the same 60/40 on/off duty cycle
            # flash at 1 Hz if there is no flow
            if flowing:
                flowThread.ZONE_MAP["led"].value = current_state
            else:
                flowThread.ZONE_MAP["led"].value = int(now) % 2

            # first pulse in a long time
            if flowing and (dt > flowThread.LEAK_DETECT_DT):
                server_log.info("Flow startup or Possible Leak")

            # log time, flow rate, and zone activation
            last_state = current_state
            now = int(time.strftime("%M"))
            if ((now != last_record_time) and flowing):   # record on the minute
                record = time.strftime("%m/%d/%Y %H:%M")+"\t%.1f"%igpm+"\t%.0f"%gallons+'\n'
                server_log.debug(record)
                record = time.strftime("%m/%d/%Y %H:%M")+"\t%.1f"%igpm+"\t%.0f"%gallons+"\t%s"%g_active_zone+'\n'
                log.debug(record)
                with open(FLOW_FILE, 'a') as f:
                    f.write(record)
                last_record_time = now
                flowing = False                     # reset flag after logging to start next minute anew

            # monitor zone control lines
            g_active_zone = "Off"
            for zone in list(flowThread.ZONE_MAP.keys())[3:]:    # ignore led, flowmeter, and pump
                if flowThread.ZONE_MAP[zone].value:
                    if g_active_zone != "Off":
                        log.warning("More than one zone active. %s, %s", (g_active_zone, zone) )
                    with g_flow_lock: g_active_zone = zone

            time.sleep(flowThread.SAMPLE_INTERVAL)


class fpLightingThread(threading.Thread):
    STD_COLOR     = { "RED" : npdrvr.COLOR_RED, "GREEN" : npdrvr.COLOR_GREEN, "BLUE" : npdrvr.COLOR_BLUE, "WHITE" : npdrvr.COLOR_WHITE }
    STD_INTENSITY = { "LOW" : npdrvr.INTENSITY_LOW, "MEDIUM" : npdrvr.INTENSITY_MEDIUM, "HIGH" : npdrvr.INTENSITY_HIGH }
    STROBE_ON_TIME      = 0.010     # 10 mS
    STROBE_INTERVAL     = 1.0       # flash every 1 second
    THROB_INTERVAL      = 4.0       # seconds from dark to set intensity and back to dark
    THROB_STEPS         = 20        # num-1 (steps include 0) of discrete intensities between dark and set intensity
    MARCH_POSTS         = 2         # MARCH patters is 2 posts on, 2 posts off, stepping 1 post per interval
    MARCH_INTERVAL      = 1.0       # post pattern marches every second
    TWINKLE_INTERVAL    = 0.5       # sec

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True
        self.light_style = ("DISPLAY", "WHITE", "LOW", "STEADY")
        self.delay = 1.0      # 10 ms
        self.color = npdrvr.COLOR_WHITE
        self.intensity = npdrvr.INTENSITY_LOW
        self.strobe = False     # True = ON (flash), False = OFF
        self.throb  = False     # True = increasing intensity, False = decreasing intensity
        self.throb_step = 1     # Must be 0 < self.throb_step < THROB_STEPS
        self.march_on   = True  # True = turn post LEDs on, False = turn post LEDs off
        self.march_step = 0

    def _colorLookup(self, color):
        if color in self.STD_COLOR:
            pixel_color = self.STD_COLOR[color]
        elif color == "RAINBOW":
            pixel_color = npdrvr.wheel(random.randint(1, 255))
        else:
            pixel_color = npdrvr.COLOR_WHITE
        return pixel_color

    def _intensityLookup(self, intensity):
        return self.STD_INTENSITY.get(intensity, npdrvr.INTENSITY_LOW)

    def run(self):
        server_log.info("fpLightingThread running")

        # set LED string to default condition
        npdrvr.set_all_pixels(self.color, self.intensity)

        while True:

            # sleep until display needs updating
            # check for incoming messages, update light_style
            # do whatever is needed to display light_style

            time.sleep(self.delay)

            try:
                msg = lighting_cmd_q.get_nowait()
                lighting_cmd_q.task_done()
                self.light_style = msg
            except:
                pass

            if self.light_style[0] == "DISPLAY" :       # message type = (DISPLAY, COLOR, INTENSITY, PATTERN)

                # display color is one of (RED, GREEN, BLUE, WHITE, RAINBOW)
                self.color = self._colorLookup(self.light_style[1])

                # display intensity is one of (LOW, MEDIUM, HIGH)
                self.intensity = self._intensityLookup(self.light_style[2])

                # display pattern is one of (STEADY, STROBE, THROB, MARCH, TWINKLE)
                if   self.light_style[3] == "STEADY":
                    self.delay = 0.1
                    npdrvr.set_all_pixels(self.color, self.intensity)

                elif self.light_style[3] == "STROBE":
                    if self.strobe:
                        self.strobe = False
                        self.intensity = npdrvr.INTENSITY_OFF
                        self.delay = self.STROBE_INTERVAL
                    else:
                        self.strobe = True
                        self.delay = self.STROBE_ON_TIME
                    npdrvr.set_all_pixels(self.color, self.intensity)

                elif self.light_style[3] == "THROB":
                    if self.throb:                              # increasing intensity
                        self.throb_step += 1
                        if self.throb_step >= self.THROB_STEPS: # full intensity
                            self.throb = False                  # now start reducing intensity
                    else:                                       # decreasing intensity
                        self.throb_step -= 1
                        if self.throb_step <= 0:                # min intensity (off)
                            self.throb = True                   # now start increasing intensity
                    # scale intensity INTENSITY_LOW -> 1
                    intensity = npdrvr.INTENSITY_LOW + (((self.intensity - npdrvr.INTENSITY_LOW) * self.throb_step) / self.THROB_STEPS)
                    npdrvr.set_all_pixels(self.color, intensity)
                    self.delay = (self.THROB_INTERVAL / 2) / (self.THROB_STEPS + 1)

                elif self.light_style[3] == "MARCH":
                    pixel_list = npdrvr.get_all_pixels()
                    for i in reversed(range(npdrvr.N_LEDS_PER_POST, npdrvr.N_LEDS_PER_STRING[0])):  # scroll pixels one post
                        pixel_list[i] = pixel_list[i-npdrvr.N_LEDS_PER_POST]

                    npdrvr.set_all_pixels(self.color, 0.0)
                    '''

                    self.march_step += 1
                    if self.march_step >= self.MARCH_POSTS:
                        self.march_step = 0
                        self.march_on = not self.march_on   # toggle on/off state of post at start of string
                    if not self.march_on:
                        self.intensity = npdrvr.INTENSITY_OFF
                    for i in range(npdrvr.N_LEDS_PER_POST):
                        pixel_list[i] = npdrvr.set_intensity(self.color, self.intensity)
                    '''

                    npdrvr.copy_all_pixels(pixel_list)
                    self.delay = self.MARCH_INTERVAL

                elif self.light_style[3] == "TWINKLE":
                    pixel_list = npdrvr.get_all_pixels()
                    n_pixels = len(pixel_list)
                    for j in range(int(n_pixels/4)):     # randomly change state of 1/4 of the pixels
                        i = random.randint(0, n_pixels-1)
                        if pixel_list[i] == (0, 0, 0):
                            pixel_list[i] = npdrvr.set_intensity(self.color, self.intensity)
                        else:
                            pixel_list[i] = (0, 0, 0)
                    npdrvr.copy_all_pixels(pixel_list)
                    self.delay = self.TWINKLE_INTERVAL

                else:   # unrecognized pattern, reset to default
                    self.light_style[3] = "STEADY"
                    self.delay = 0.0
                    server_log.warning("Unrecognized lighting pattern = %s", self.light_style[3])

            elif self.light_style[0] == "LIGHTING":       # message type = (LIGHTING, FENCEPOST NUMBER, ORIENTATION, COLOR, BRIGHTNESS)
                self.color = self.light_style[3]
                self.intensity = self.light_style[4]
                pixel_list = npdrvr.get_all_pixels()
                i_start = pixel_index(int(int(self.light_style[1])), self.light_style[2], position=1)
                for i in range(i_start, i_start+npdrvr.N_LEDS_PER_POST):
                    pixel_list[i] = npdrvr.set_intensity(self.color, self.intensity)
                npdrvr.copy_all_pixels(pixel_list)

            else:   # unrecognized type, reset to default
                self.light_style = ("DISPLAY", "WHITE", "LOW", "STEADY")
                self.delay = 0.0
                server_log.warning("Unrecognized lighting message type = %s", self.light_style[0])

class healthThread(threading.Thread):
    HEARTBEAT_INTERVAL = 60    # report health every minute
    REMOTE_URL = "http://mindmentum.com/cgi-bin/ha.py"
    REMOTE_REQUEST = 'curl -X POST -H "Content-Type: application/json" -d '

    def __init__(self, host, node_t):
        threading.Thread.__init__(self)
        host_name = host
        node_type = node_t
        self.daemon = True

    def run(self):
        server_log.info("healthThread running")

        while True:
            health_status = "'{ 'device' : '" + host_name + "' }'"  # dictionary of health related parameters

            # report health to remote server
            # os.system('curl -X POST -H "Content-Type: application/json" -d \'{"device": "flowmeter"}\' http://mindmentum.com/cgi-bin/ha.py > /dev/null 2>&1')
            os.system(REMOTE_REQUEST + health_status + REMOTE_URL + " > /dev/null 2>&1")

            # report health to magic mirror
            msg = ("HEALTH_NOTICE", health_status)  # message must be a list
            s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                s.connect(("magicmirror", HOME_AUTOMATION_PORT))
            except:
                server_log.warning("healthThread failed to report to magic mirror")
            else:
                s.sendall(pickle.dumps(msg, pickle.HIGHEST_PROTOCOL))
                s.close()

            time.sleep(healthThread.HEARTBEAT_INTERVAL)



class serverThread(threading.Thread):
    def __init__(self, node_t):
        threading.Thread.__init__(self)
        node_type = node_t
        self.daemon = True

    def run(self):
        global g_vi_latest
        global g_vi_lock
        global g_flow_latest
        global g_flow_lock

        server_log.info("serverThread running")

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(('', HOME_AUTOMATION_PORT)) # listen on all IP addresses on this host
        s.listen(5)
        server_log.info("Listening on port (%s, %d)", "''", HOME_AUTOMATION_PORT)

        while True:
            buf = b''
            client, addr = s.accept()   # block until connection request
            while True:
                data = client.recv(4096)
                if data:
                    buf += data
                if not data:
                    # client has sent message and shut down connection
                    break

            msg = pickle.loads(buf) # depickle network message back to a message list
            server_log.debug("Received message: %s", str(msg))
            msg_t = msg[0]

            # validate message can be handled by this node type
            if msg_t not in MSG_TYPES:
                server_log.warning("Unknown message type received: %s" % msg_t)
            else:
                if node_type not in MSG_TYPES[msg_t]:
                    server_log.warning("Message type %s cannot be handled by this node type" % msg_t)

                else:   # decode and respond to message
                    if msg_t == "VI_QUERY":
                        # fetch global variable with latest vi sample
                        with g_vi_lock:
                            (vin, cur) = g_vi_latest
                        client.sendall(pickle.dumps((vin, cur), pickle.HIGHEST_PROTOCOL))

                    elif msg_t == "SOUND":
                        # get mp3 file and drive output
                        pass

                    elif msg_t == "VI_HISTORY":
                        vi_list = []
                        if not vi_q.empty():
                            vi_list.append(vi_q.get_nowait())
                        client.sendall(pickle.dumps(vi_list, pickle.HIGHEST_PROTOCOL))

                    elif msg_t == "DISPLAY":
                        lighting_cmd_q.put(msg)

                    elif msg_t == "FLOW_QUERY":
                        # fetch global variable with latest flow sample and zone activation
                        with g_flow_lock:
                            (gpm, gal) = g_flow_latest
                            zone = g_active_zone
                        client.sendall(pickle.dumps((gpm, gal, zone), pickle.HIGHEST_PROTOCOL))

                    elif msg_t == "FLOW_HISTORY":
                        with open('flowrecord.txt', 'r') as f:
                            history = f.readlines()
                            client.sendall(pickle.dumps(history.reverse(), pickle.HIGHEST_PROTOCOL))

                    elif msg_t == "HEALTH_NOTICE":
                        mm.nodeStatusHandler(msg)
                        pass

            client.close()


if __name__ == "__main__":

    host_name = socket.gethostname()
    if not host_name in SYSTEM_HOSTS:
        node_type = 'unknown: ' + host_name
    else:
        node_type = host_name.split('-')[0]

    # magicmirror node has additional functionality and incorporates a communication
    # channed to the magicmirror Javascript app
    if node_type == 'magicmirror':
        import han_mm as mm

    # log configuration
    log_datefmt = '%m/%d/%Y %H:%M:%S '
    log_format ='%(asctime)s ' + host_name + ' %(levelname)s %(message)s'
    log_formatter = logging.Formatter(fmt=log_format, datefmt=log_datefmt)

    # 256K max file size, 4 files max
    master_log_fh = logging.handlers.RotatingFileHandler(MASTER_LOG, maxBytes=(256*1024), backupCount=3)
    server_log_fh = logging.handlers.RotatingFileHandler(SERVER_LOG, maxBytes=(256*1024), backupCount=3)
    flow_log_fh   = logging.handlers.RotatingFileHandler(FLOW_LOG, maxBytes=(256*1024), backupCount=3)
    vi_log_fh     = logging.handlers.RotatingFileHandler(VI_LOG, maxBytes=(256*1024), backupCount=3)

    # master_log (root) records eveything, level='DEBUG'
    server_log_fh.setLevel('INFO')
    server_log_fh.setFormatter(log_formatter)
    flow_log_fh.setLevel('INFO')
    flow_log_fh.setFormatter(log_formatter)
    vi_log_fh.setLevel('INFO')
    vi_log_fh.setFormatter(log_formatter)

    # configure and instantiate loggers
    logging.basicConfig(format=log_format, datefmt=log_datefmt, handlers=(logging.StreamHandler(), master_log_fh), level='DEBUG')
    master_log = logging.getLogger('han')               # all nodes
    server_log = logging.getLogger('han.server')
    server_log.addHandler(server_log_fh)

    if (node_type == 'flowmeter') or (node_type == 'fencepost'):
        vi_log = logging.getLogger('han.vi')
        vi_log.addHandler(vi_log_fh)

        if node_type == 'flowmeter':
            flow_log = logging.getLogger('han.flow')
            flow_log.addHandler(flow_log_fh)

    server_log.info("")
    server_log.info("SERVER STARTING...")
    server_log.info("Host name is %s", host_name)
    server_log.info("Node type is %s", node_type)

    # start threads
    if node_type in MSG_TYPES['DISPLAY']:
        fpl_t = fpLightingThread()
        fpl_t.start()
    if node_type in MSG_TYPES['VI_QUERY']:
        vi_t = viThread()
        vi_t.start()
    if node_type in MSG_TYPES['FLOW_QUERY']:
        flow_t = flowThread()
        flow_t.start()
    if node_type in MSG_TYPES['PLAY_AUDIO']:
        audio_t = audioThread()
        audio_t.start()
    if node_type == 'magicmirror':
        mm.start()   # start mm.threadfunction_t ...
        pass

    health_t = healthThread(host_name, node_type)
    health_t.start()

    server_t = serverThread(node_type)
    server_t.start()

    while True:
        pass
