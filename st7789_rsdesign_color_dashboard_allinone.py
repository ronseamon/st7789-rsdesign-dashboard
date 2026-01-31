#!/usr/bin/env python3
# ST7789 172x320 RSDESIGN Color Dashboard (ALL-IN-ONE, STABLE)

import os, time, socket, math, datetime
import psutil, requests, spidev
from gpiozero import DigitalOutputDevice
from PIL import Image, ImageDraw, ImageFont

# ================= USER CONFIG =================
WIDTH, HEIGHT = 172, 320
X_OFFSET, Y_OFFSET = 34, 0
SPI_BUS, SPI_DEV = 0, 0
SPI_HZ = 24_000_000
PAGE_TIME = 6.0

CITY = os.getenv("CITY", "Gig Harbor,US")
UNITS = os.getenv("UNITS", "imperial")
OWM_API_KEY = os.getenv("OWM_API_KEY")
ICON_DIR = "/home/ronseamon/weather_icons"

DC_PIN = 25
RST_PIN = 24

# ================= SPI / GPIO =================
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEV)
spi.max_speed_hz = SPI_HZ
spi.mode = 0

dc = DigitalOutputDevice(DC_PIN)
rst = DigitalOutputDevice(RST_PIN)

# ================= ST7789 LOW LEVEL =================
def cmd(c):
    dc.off()
    spi.writebytes([c])

def data(buf):
    dc.on()
    for i in range(0, len(buf), 4096):
        spi.writebytes2(buf[i:i+4096])

def reset():
    rst.on(); time.sleep(0.05)
    rst.off(); time.sleep(0.05)
    rst.on(); time.sleep(0.15)

def init_display():
    reset()
    cmd(0x01); time.sleep(0.15)
    cmd(0x11); time.sleep(0.15)
    cmd(0x3A); data(b"\x55")
    cmd(0x36); data(b"\x08")
    cmd(0x21)
    cmd(0x29)

def window():
    cmd(0x2A)
    data(bytes([0, X_OFFSET, 0, X_OFFSET + WIDTH - 1]))
    cmd(0x2B)
    data(bytes([0, Y_OFFSET, (HEIGHT-1)>>8, (HEIGHT-1)&0xFF]))
    cmd(0x2C)

def rgb565(img):
    raw = img.tobytes()
    out = bytearray(len(raw)//3 * 2)
    j = 0
    for i in range(0, len(raw), 3):
        r, g, b = raw[i:i+3]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j] = v >> 8
        out[j+1] = v & 0xFF
        j += 2
    return out

def push(img):
    window()
    data(rgb565(img))

# ================= UTIL =================
def ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        v = s.getsockname()[0]
        s.close()
        return v
    except:
        return "0.0.0.0"

def temp():
    try:
        t = int(open("/sys/class/thermal/thermal_zone0/temp").read())/1000
        return f"{t:.1f}°"
    except:
        return "N/A"

def uptime():
    u = float(open("/proc/uptime").read().split()[0])
    h = int(u//3600)
    m = int((u%3600)//60)
    return f"{h:02}:{m:02}"

def grad(p):
    p = max(0, min(100, p)) / 100
    if p < 0.5:
        return (int(510*p), 255, 0)
    return (255, int(255*(1-(p-0.5)*2)), 0)

# ================= WEATHER =================
def fetch_weather():
    if not OWM_API_KEY:
        return None
    try:
        r = requests.get(
            "https://api.openweathermap.org/data/2.5/weather",
            params={"q": CITY, "appid": OWM_API_KEY, "units": UNITS},
            timeout=8
        )
        if r.status_code != 200:
            return None
        return r.json()
    except:
        return None

def load_weather_icon(code):
    path = os.path.join(ICON_DIR, f"{code}.png")
    if not os.path.exists(path):
        return None
    return Image.open(path).convert("RGBA")

# ================= FONTS =================
FONT_L = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)
FONT_M = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 16)
FONT_S = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 13)

# ================= DRAW HELPERS =================
def ring(draw, cx, cy, r, w, pct, label):
    col = grad(pct)
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(80,80,80), width=2)
    sweep = int(360 * pct / 100)
    for i in range(w):
        draw.arc([cx-r+i, cy-r+i, cx+r-i, cy+r-i], -90, -90+sweep, fill=col)
    draw.text((cx-20, cy-10), f"{int(pct)}%", font=FONT_M, fill=(255,255,255))
    draw.text((cx-18, cy+14), label, font=FONT_S, fill=(200,200,200))

# ================= PAGES =================
def page_perf():
    img = Image.new("RGB", (WIDTH, HEIGHT), (0,0,0))
    d = ImageDraw.Draw(img)

    d.text((10,6), "RSDESIGN", font=FONT_L, fill=(255,255,255))
    ring(d, WIDTH//2, 110, 56, 10, psutil.cpu_percent(), "CPU")
    ring(d, WIDTH//2, 220, 56, 10, psutil.virtual_memory().percent, "RAM")

    return img

def page_status():
    img = Image.new("RGB", (WIDTH, HEIGHT), (0,0,0))
    d = ImageDraw.Draw(img)

    d.text((10,6), "STATUS", font=FONT_L, fill=(255,255,255))
    d.text((10,60), f"IP  {ip()}", font=FONT_M, fill=(200,200,200))
    d.text((10,90), f"UP  {uptime()}", font=FONT_M, fill=(200,200,200))
    d.text((10,120), f"TEMP {temp()}", font=FONT_M, fill=(200,200,200))

    return img

def page_weather():
    img = Image.new("RGB", (WIDTH, HEIGHT), (0,0,0))
    d = ImageDraw.Draw(img)

    d.text((10,6), "WEATHER", font=FONT_L, fill=(255,255,255))
    d.text((10,34), CITY.upper(), font=FONT_M, fill=(180,180,180))

    w = fetch_weather()
    if not w:
        d.text((20,150), "Weather unavailable", font=FONT_M, fill=(200,0,0))
        return img

    icon_code = w["weather"][0]["icon"]
    icon = load_weather_icon(icon_code)
    if icon:
        icon = icon.resize((80,80), Image.LANCZOS)
        img.paste(icon, (46, 60))

    t = int(w["main"]["temp"])
    feels = int(w["main"]["feels_like"])
    cond = w["weather"][0]["main"]

    d.text((46, 150), f"{t}°F", font=FONT_L, fill=(255,255,255))
    d.text((40, 180), f"Feels {feels}°", font=FONT_S, fill=(180,180,180))
    d.text((40, 205), cond, font=FONT_S, fill=(180,180,180))

    return img

# ================= MAIN =================
init_display()
psutil.cpu_percent()

pages = [page_perf, page_status, page_weather]
idx = 0

while True:
    push(pages[idx]())
    idx = (idx + 1) % len(pages)
    time.sleep(PAGE_TIME)
