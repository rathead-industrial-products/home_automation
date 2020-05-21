#!/usr/bin/python
#

"""

Universal code base for Raspberry Pi based home automation nodes.

1. Fencepost Lighting Controller
2. Voltage/Current Sampler
3. Hunter Flow Meter Monitor
4. TCP/IP Socket Server

The nodes have a Pi Bonnet that contains a power supply, an RS-422
driver for daisy-chaining LED strings, and an I2C ADC that monitors
the supply voltage and the load current (at 5V) of the node.

The Flow meter repurposes the LED driver GPIO to interface to the
reed relay interruptor in the flow sensor.

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

LOG_FILENAME = "log.txt"

lighting_cmd_q = queue.Queue()          # unbounded, but will empty as soon as a record is added
vi_q           = queue.Queue(10000)     # a week's worth of samples at 1 sample/min

g_vi_latest    = (0, 0)                 # global variable containing latest (v, i) sample
g_vi_lock      = threading.Lock()

g_flow_latest  = (1, 2)                 # global variable containing latest (gpm, gal) sample
g_flow_lock    = threading.Lock()


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
        log.info("fpLightingThread running")

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
                    log.warning("Unrecognized lighting pattern = %s", self.light_style[3])

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
                log.warning("Unrecognized lighting message type = %s", self.light_style[0])


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

        log.info("viThread running")

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
                log.error("Unable to add record to vi_q")

            # update global variable with latest sample
            with g_vi_lock:
                g_vi_latest = (vin, cur)

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
    #   A logfile records cumulative gallons every minute when water is flowing.
    #   Each record is a line in the file of format <timestamp> cumgal.x gpm.x
    #

    SAMPLE_INTERVAL = .050    # sample flow pulse every 50 ms

    def __init__(self):
        threading.Thread.__init__(self)
        self.daemon = True

    def run(self):
        global g_flow_latest
        global g_flow_lock

        log.info("flowThread running")

        flow_meter = digitalio.DigitalInOut(board.D18)
        flow_meter.direction = digitalio.Direction.INPUT
        flow_meter.pull = digitalio.Pull.UP

        gallons    = 0.0
        igpm       = 0.0
        last_state = False
        last_pulse = 0
        last_record_time = 0
        flowing    = False         # flowmeter activity detected

        while True:
            current_state = flow_meter.value        # pulse line is high or low
            if (not last_state) and current_state:  # on rising edge of pulse
                flowing = True                      # flowmeter activity detected
                this_pulse = time.monotonic()       # to determine e.t. since last pulse
                gallons += 0.1
                if last_pulse != 0:                 # avoid bogus value at startup
                    igpm = 60 * (0.1/(this_pulse - last_pulse)) # instantaneous GPM
                last_pulse = this_pulse

                # update global variable with latest sample
                with g_flow_lock:
                    g_flow_latest = (igpm, gallons)

            last_state = current_state
            now = int(time.strftime("%M"))
            if ((now != last_record_time) and flowing):   # record on the minute
                record = time.strftime("%m/%d/%Y %H:%M")+"\t%.1f"%igpm+"\t%.0f"%gallons+'\n'
                log.info(record)
                with open('flowrecord.txt', 'a') as f:
                    f.write(record)
                last_record_time = now
                flowing = False                     # reset flag

            time.sleep(flowThread.SAMPLE_INTERVAL)






HOST = ''           # Listen on all IP addresses on this host
PORT = 6554         # Port to listen on (non-privileged ports are > 1023)

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

        log.info("serverThread running")

        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind((HOST, PORT))
        s.listen(5)
        log.info("Listening on port (%s, %d)", HOST, PORT)

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
            log.info("Received message: %s", str(msg))

            #
            # decode and respond to message
            #
            if msg[0] == "VI_QUERY":
                # fetch global variable with latest vi sample
                with g_vi_lock:
                    (vin, cur) = g_vi_latest
                client.sendall(pickle.dumps((vin, cur), pickle.HIGHEST_PROTOCOL))

            elif msg[0] == "VI_HISTORY":
                vi_list = []
                if not vi_q.empty():
                    vi_list.append(vi_q.get_nowait())
                client.sendall(pickle.dumps(vi_list, pickle.HIGHEST_PROTOCOL))

            elif ((node_type == "Fencepost") and ((msg[0] == "LIGHTING") or (msg[0] == "DISPLAY"))):
                lighting_cmd_q.put(msg)

            elif ((node_type == "Flowmeter") and (msg[0] == "FLOW_QUERY")):
                # fetch global variable with latest flow sample
                with g_flow_lock:
                    (gpm, gal) = g_flow_latest
                client.sendall(pickle.dumps((gpm, gal), pickle.HIGHEST_PROTOCOL))

            elif ((node_type == "Flowmeter") and (msg[0] == "FLOW_HISTORY")):
                with open('flowrecord.txt', 'r') as f:
                    history = f.readlines()
                    client.sendall(pickle.dumps(history.reverse(), pickle.HIGHEST_PROTOCOL))

            else:
                log.warning("Unknown message type received by %s node: %s" % (node_type, msg[0]))

            client.close()




if __name__ == "__main__":

    # Set up a logger
    log_format  ='%(asctime)s %(message)s'
    log_datefmt ='%m/%d/%Y %H:%M:%S '
    log_file_handler = logging.handlers.RotatingFileHandler(LOG_FILENAME, maxBytes=(256*1024), backupCount=3)    # 256K max file size, 4 files max
    log_level   = logging.DEBUG
    logging.basicConfig(format=log_format, datefmt=log_datefmt, handlers=(logging.StreamHandler(), log_file_handler), level=log_level)
    log = logging.getLogger('')


    #
    # Node will either be a fencepost light or a flowmeter
    # Fencepost lights are powered by 6-15 VDC
    # Flowmeter is powered by 24 VAC
    #

    vi_t = viThread()
    vi_t.start()

    # Look at the input voltage to determine node type

    (vin, cur) = vi_q.get(timeout=65)    # give thread time to sample
    if vin < 20:  # fencepost node
        fpl_t = fpLightingThread()
        fpl_t.start()
        node_type = "Fencepost"
    else:           # flowmeter node
        flow_t = flowThread()
        flow_t.start()
        node_type = "Flowmeter"
    log.info("Input voltage = %.1f, node type is %s", vin, node_type)

    server_t = serverThread(node_type)
    server_t.start()

    while True:
        pass
