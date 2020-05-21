#!/usr/bin/python
#

"""

Rasperry Pi NeoPixel driver for fencepost lighting

Fenceposts are lit on four sides, 'N', 'S', 'E', and 'W'
A single LED string lights one or more fenceposts
A set of fenceposts will have one or more LED strings

The array of pixels in an LED string are 0 based.
The array of fenceposts is 1 based.

The strings are mechanically arranged as follows:

Fencepost Number    -------------------------------------------- 1 --------------------------------------------------    ----------------- 2 --- ...
Fencepost Side      -------- N --------     -------- W --------     --------- E ---------     ---------- S ----------    -------- N --------     ...
string_n ->         p[0],p[1],p[2],p[3]     p[4],p[5],p[6],p[7]     p[8],p[9],p[10],p[11]     p[12],p[13],p[14],p[15]    p[16],p[17],p[17],p[19] ...

"""

import time
import board
import neopixel

N_FENCEPOSTS        = 1
N_LEDS_PER_POST     = 16
N_LEDS_PER_SIDE     = 4
N_STRINGS           = 4
N_LEDS_PER_STRING   = (16, 16, 16, 16)  # may not always be equal


# The order of the pixel colors - RGB or GRB or RGBW or GRBW
ORDER = neopixel.GRBW

if ORDER in (neopixel.RGB, neopixel.GRB):
    # RGB REPRESENTATION
    COLOR_RED           = (255,   0,   0)
    COLOR_GREEN         = (  0, 255,   0)
    COLOR_BLUE          = (  0,   0, 255)
    COLOR_WHITE         = (255, 255, 255)
    COLOR_BLACK         = (  0,   0,   0)
    COLOR_LIST_ALL      = (COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_BLUE, COLOR_WHITE)
else:
    # RGBW REPRESENTATION
    COLOR_RED           = (255,   0,   0,   0)
    COLOR_GREEN         = (  0, 255,   0,   0)
    COLOR_BLUE          = (  0,   0, 255,   0)
    COLOR_WHITE         = (  0,   0,   0, 255)
    COLOR_BLACK         = (  0,   0,   0,   0)
    COLOR_LIST_ALL      = (COLOR_BLACK, COLOR_RED, COLOR_GREEN, COLOR_BLUE, COLOR_WHITE)


# 0 <= intensity <= 1
INTENSITY_OFF       = 0.00
INTENSITY_LOW       = 0.25
INTENSITY_MEDIUM    = 0.50
INTENSITY_HIGH      = 1.00

# define system configuration
# table of fencepost number, led string, first led in string at fencepost
CONFIGURATION = ( ( 1, 1, 1  ),
                  ( 2, 1, 16 ),
                  ( 3, 1, 32 ),
                  ( 4, 1, 46 ),
                  ( 5, 2, 1  ),
                  ( 6, 2, 16 ),
                  ( 7, 2, 32 ),
                  ( 8, 2, 46 ) )


# Raspberry pi
pixel_pin = board.D18

# The number of NeoPixels
num_pixels = N_FENCEPOSTS * N_LEDS_PER_POST

pixels = neopixel.NeoPixel(pixel_pin, num_pixels, brightness=0.2, auto_write=False, pixel_order=ORDER)

gamma = ( 0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,
          0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  0,  1,  1,  1,  1,
          1,  1,  1,  1,  1,  1,  1,  1,  1,  2,  2,  2,  2,  2,  2,  2,
          2,  3,  3,  3,  3,  3,  3,  3,  4,  4,  4,  4,  4,  5,  5,  5,
          5,  6,  6,  6,  6,  7,  7,  7,  7,  8,  8,  8,  9,  9,  9, 10,
         10, 10, 11, 11, 11, 12, 12, 13, 13, 13, 14, 14, 15, 15, 16, 16,
         17, 17, 18, 18, 19, 19, 20, 20, 21, 21, 22, 22, 23, 24, 24, 25,
         25, 26, 27, 27, 28, 29, 29, 30, 31, 32, 32, 33, 34, 35, 35, 36,
         37, 38, 39, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50, 50,
         51, 52, 54, 55, 56, 57, 58, 59, 60, 61, 62, 63, 64, 66, 67, 68,
         69, 70, 72, 73, 74, 75, 77, 78, 79, 81, 82, 83, 85, 86, 87, 89,
         90, 92, 93, 95, 96, 98, 99,101,102,104,105,107,109,110,112,114,
        115,117,119,120,122,124,126,127,129,131,133,135,137,138,140,142,
        144,146,148,150,152,154,156,158,160,162,164,167,169,171,173,175,
        177,180,182,184,186,189,191,193,196,198,200,203,205,208,210,213,
        215,218,220,223,225,228,231,233,236,239,241,244,247,249,252,255 )


# return the led string containing the specified pixel
def led_string(post, side='N', position=1):
    pass

# return the pixel index in the led string of the specified pixel
def pixel_index(post, side='N', position=1):
    pass

# scale and gamma correct the value of the color tuple by intensity, where 0 <= intensity <= 1
def set_intensity(color_tuple, intensity):
    gci = gamma[int((255*intensity) + 0.5)]/255.0
    output_pixel = ()
    for i in range(len(color_tuple)):   # auto-determine RGB or RGBW
        output_pixel += (int(color_tuple[i]*gci),)
    return output_pixel

def wheel(pos):
    # Input a value 0 to 255 to get a color value.
    # The colours are a transition r - g - b - back to r.
    if pos < 0 or pos > 255:
        r = g = b = 0
    elif pos < 85:
        r = int(pos * 3)
        g = int(255 - pos * 3)
        b = 0
    elif pos < 170:
        pos -= 85
        r = int(255 - pos * 3)
        g = 0
        b = int(pos * 3)
    else:
        pos -= 170
        r = 0
        g = int(pos * 3)
        b = int(255 - pos * 3)
    return (r, g, b) if ORDER in (neopixel.RGB, neopixel.GRB) else (r, g, b, 0)

def set_pixel(post, side, position, color, intensity):
    pixels[pixel_index(post, side, position)] = set_intensity(color, intensity)
    pixels.show()

def set_all_pixels(color, intensity):
    pixels.fill(set_intensity(color, intensity))
    pixels.show()

def get_all_pixels():
        pixel_list = []
        for i in range(N_LEDS_PER_STRING[0]):  # must expand to support multiple strings
            pixel_list.append(pixels[i])
        return pixel_list

def copy_all_pixels(pixel_list):
    for i in range(N_LEDS_PER_STRING[0]):  # must expand to support multiple strings
        if i >= len(pixel_list):
            pixels[i] = COLOR_BLACK
        else:
            pixels[i] = pixel_list[i]
    pixels.show()
