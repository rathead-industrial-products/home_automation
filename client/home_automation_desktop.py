#!/usr/bin/python
#

"""

Desktop interface to the home automation nodes

"""

import tkinter as tk
import tk_tools
from   tk_tools.images import rotary_gauge_volt

import socket
import pickle

FENCEPOST = '192.168.1.122'
FLOWMETER = '10.10.10.113'


HOME_AUTOMATION_PORT = 6554         # The port used by the server

# Display parameters
colors      = ["RED","GREEN","BLUE","WHITE","RAINBOW"]
intensities = ["LOW","MEDIUM","HIGH"]
patterns    = ["STEADY","STROBE","THROB","MARCH","TWINKLE"]


def get_vi():
    outb = b''
    msg = ("VI_QUERY", )      # message must be a list

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((FLOWMETER, HOME_AUTOMATION_PORT))
    except:
        print('Connect attempt failed')

    else:
        s.sendall(pickle.dumps(msg, pickle.HIGHEST_PROTOCOL))
        s.shutdown(socket.SHUT_WR) # Tell server message is complete, client still available to read response

        while True:
            data = s.recv(1024)
            if data:
                outb += data
            if not data:
                break

        (power.volts, power.ma) = pickle.loads(outb)
        s.close()

        power["text"] = "%.1f V, %.1f mA" % (power.volts, power.ma)

    root.after(60*1000, get_vi) # sample every minute


def get_flow():
    outb = b''
    msg = ("FLOW_QUERY", )      # message must be a list

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((FLOWMETER, HOME_AUTOMATION_PORT))
    except:
        print('Connect attempt failed')

    else:
        s.sendall(pickle.dumps(msg, pickle.HIGHEST_PROTOCOL))
        s.shutdown(socket.SHUT_WR) # Tell server message is complete, client still available to read response

        meter.gpm = 0.0

        while True:
            data = s.recv(1024)
            if data:
                outb += data
            if not data:
                break

        (meter.gpm, totalizer.gal) = pickle.loads(outb)
        s.close()

        meter.set_value(int(meter.gpm*10)/10)
        totalizer["text"] = "%.1f gallons" % totalizer.gal

    root.after(1000, get_flow)


def updateDisplay():
    msg = ("DISPLAY", colors[color_rb.get()], intensities[intensity_rb.get()], patterns[pattern_rb.get()])
    print (msg)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect((FLOWMETER, HOME_AUTOMATION_PORT))
    except:
        print('Connect attempt failed')

    else:
        s.sendall(pickle.dumps(msg, pickle.HIGHEST_PROTOCOL))
        s.close()


root = tk.Tk()

#
# Set up flowmieter window
#

root.title("Flowmeter")
root.geometry('640x480')
meter = tk_tools.RotaryScale(root,
                          max_value=20,
                          size=100,
                          unit=' gpm',
                          needle_thickness=3,
                          needle_color='black',
                          img_data=rotary_gauge_volt)
meter.grid(row=0, column=0)
meter.gpm = 0.0

totalizer = tk.Label(root)
totalizer.grid(column=0, row=1)
totalizer.gal = 0.0

power = tk.Label(root)
power.grid(column=0, row=2)
power.volts = 0.0
power.ma    = 0.0


#
# Set up fencepost lighting
#

# Radio Buttons
color_rb = tk.IntVar()
color_rb.set(3)  # initializing the choice to WHITE
tk.Label(root, text="""COLOR""", justify = tk.LEFT, padx = 20).grid(column=3, row=0)
rb_row = 1
for val, color in enumerate(colors):
    tk.Radiobutton(root, text=color, padx = 20, variable=color_rb, command=updateDisplay, value=val).grid(column=3, row=rb_row, sticky='W')
    rb_row += 1

intensity_rb = tk.IntVar()
intensity_rb.set(0)  # initializing the choice to LOW
tk.Label(root, text="""INTENSITY""", justify = tk.LEFT, padx = 20).grid(column=4, row=0)
rb_row = 1
for val, intensity in enumerate(intensities):
    tk.Radiobutton(root, text=intensity, padx = 20, variable=intensity_rb, command=updateDisplay, value=val).grid(column=4, row=rb_row, sticky='W')
    rb_row += 1

pattern_rb = tk.IntVar()
pattern_rb.set(0)  # initializing the choice to STEADY
tk.Label(root, text="""PATTERN""", justify = tk.LEFT, padx = 20).grid(column=5, row=0)
rb_row = 1
for val, pattern in enumerate(patterns):
    tk.Radiobutton(root, text=pattern, padx = 20, variable=pattern_rb, command=updateDisplay, value=val).grid(column=5, row=rb_row, sticky='W')
    rb_row += 1


root.after(0, get_flow)
root.after(0, get_vi)
root.mainloop()
