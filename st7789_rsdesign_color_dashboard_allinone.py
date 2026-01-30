#!/usr/bin/env python3
# ST7789 172x320 (SPI, mipi-dbi offset) - RSDESIGN "PironMan-style" Color Dashboard
# Pages:
#   1) PERF: CPU/RAM rings
#   2) STATUS: IP/Uptime/Temp + Disk/Mem bars + network graph (bottom 2/3)
#   3) WEATHER: iPhone-ish layout with icon, temp, feels, details, hourly strip, tomorrow hi/lo,
#              sunrise/sunset arc, severe banner, accent colors, and a subtle animated icon
#
# Requirements (venv):
#   pip install psutil requests pillow spidev gpiozero
#
# Weather:
#   export OWM_API_KEY="your_key_here"
# Icons:
#   This script expects icons in: /home/ronseamon/weather_icons/
#   Filenames like: 10n.png, 01d.png, etc (NO @2x in name)
#
# Wiring (as you described):
#   VCC->3V3, GND->GND
#   SCL->GPIO23 (SCLK)
#   SDA->GPIO10 (MOSI)
#   CS -> CE0 (GPIO8)
#   DC -> GPIO25
#   RES-> GPIO24
#   BL optional (leave or tie high depending on your board)

import os, time, math, socket, datetime
import psutil, requests, spidev
from gpiozero import DigitalOutputDevice
from PIL import Image, ImageDraw, ImageFont

# ================= PANEL =================
WIDTH, HEIGHT = 172, 320
X_OFFSET, Y_OFFSET = 34, 0          # your working offsets
SPI_BUS, SPI_DEV = 0, 0
SPI_HZ = 24_000_000
PAGE_TIME = 6.0                      # seconds per page (non-weather)
WEATHER_FRAME_HZ = 6.0               # subtle animation only on weather page (low flicker)
ICON_DIR = "/home/ronseamon/weather_icons"

DC_PIN = 25
RST_PIN = 24

CITY_Q = "Gig Harbor,US"
TZ_OFFSET_FALLBACK = -28800          # PST fallback if API missing

# ================= SPI + GPIO =================
spi = spidev.SpiDev()
spi.open(SPI_BUS, SPI_DEV)
spi.max_speed_hz = SPI_HZ
spi.mode = 0

dc  = DigitalOutputDevice(DC_PIN)
rst = DigitalOutputDevice(RST_PIN)

# ================= ST7789 low-level =================
def cmd(c: int):
    dc.off()
    spi.writebytes([c & 0xFF])

def data(b: bytes | bytearray):
    dc.on()
    # spidev prefers bytes-like; writebytes2 handles bytearray efficiently
    for i in range(0, len(b), 4096):
        spi.writebytes2(b[i:i+4096])

def reset():
    rst.on();  time.sleep(0.05)
    rst.off(); time.sleep(0.05)
    rst.on();  time.sleep(0.15)

def init_display():
    reset()
    cmd(0x01); time.sleep(0.15)  # SWRESET
    cmd(0x11); time.sleep(0.15)  # SLPOUT
    cmd(0x3A); data(b"\x55")     # COLMOD 16-bit
    # MADCTL: 0x08 = BGR. Keep stable orientation; we do offsets in window().
    cmd(0x36); data(b"\x08")
    cmd(0x21)                    # INVON (often needed for ST7789 variants)
    cmd(0x29)                    # DISPON
    time.sleep(0.05)

def set_window():
    # Column
    cmd(0x2A)
    x0 = X_OFFSET
    x1 = X_OFFSET + WIDTH - 1
    data(bytes([(x0 >> 8) & 0xFF, x0 & 0xFF, (x1 >> 8) & 0xFF, x1 & 0xFF]))
    # Row
    cmd(0x2B)
    y0 = Y_OFFSET
    y1 = Y_OFFSET + HEIGHT - 1
    data(bytes([(y0 >> 8) & 0xFF, y0 & 0xFF, (y1 >> 8) & 0xFF, y1 & 0xFF]))
    # RAMWR
    cmd(0x2C)

def rgb565(img: Image.Image) -> bytearray:
    # PIL RGB -> RGB565 big-endian
    rgb = img.convert("RGB").tobytes()
    out = bytearray((len(rgb) // 3) * 2)
    j = 0
    for i in range(0, len(rgb), 3):
        r, g, b = rgb[i], rgb[i+1], rgb[i+2]
        v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
        out[j]   = (v >> 8) & 0xFF
        out[j+1] = v & 0xFF
        j += 2
    return out

def push(img: Image.Image):
    set_window()
    data(rgb565(img))

# ================= Fonts =================
def load_font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except:
        return ImageFont.load_default()

FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

F_XL = load_font(FONT_BOLD, 24)
F_L  = load_font(FONT_BOLD, 20)
F_M  = load_font(FONT_PATH, 16)
F_S  = load_font(FONT_PATH, 13)
F_XS = load_font(FONT_PATH, 11)

# ================= General UI helpers =================
BLACK = (0,0,0)
WHITE = (255,255,255)
GREY  = (80,80,80)
LITE  = (190,190,190)

def clamp(v, a, b):
    return a if v < a else b if v > b else v

def grad_pct(pct: float):
    # green->yellow->red
    p = clamp(pct/100.0, 0.0, 1.0)
    if p < 0.5:
        return (int(510*p), 255, 0)
    return (255, int(255*(1 - (p-0.5)*2)), 0)

def text_center(draw, y, s, font, fill, x0=0, x1=WIDTH):
    w = draw.textlength(s, font=font)
    x = int((x0 + x1 - w) / 2)
    draw.text((x, y), s, font=font, fill=fill)

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        v = s.getsockname()[0]
        s.close()
        return v
    except:
        return "0.0.0.0"

def get_uptime_hm():
    try:
        u = float(open("/proc/uptime").read().split()[0])
        h = int(u // 3600)
        m = int((u % 3600) // 60)
        return f"{h:02}:{m:02}"
    except:
        return "??:??"

def get_temp_f():
    # CPU temp
    try:
        t = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000.0
        return (t * 9.0/5.0) + 32.0
    except:
        return None

def now_local(ts=None, tz_offset=None):
    if ts is None:
        ts = time.time()
    if tz_offset is None:
        tz_offset = TZ_OFFSET_FALLBACK
    return datetime.datetime.utcfromtimestamp(ts + tz_offset)

# ================= Rings / Bars =================
def draw_ring(draw, cx, cy, r, w, pct, label, accent=(0,180,255)):
    # Base
    draw.ellipse([cx-r, cy-r, cx+r, cy+r], outline=(60,60,60), width=2)
    # Sweep
    sweep = int(360 * clamp(pct/100.0, 0.0, 1.0))
    col = grad_pct(pct)
    # ring thickness as multiple arcs (stable, no flicker)
    for i in range(w):
        draw.arc([cx-r+i, cy-r+i, cx+r-i, cy+r-i], -90, -90 + sweep, fill=col)
    # Text
    text_center(draw, cy-16, f"{int(pct)}%", F_L, WHITE, cx-r, cx+r)
    text_center(draw, cy+8, label, F_S, (210,210,210), cx-r, cx+r)

def draw_bar(draw, x, y, w, h, pct, label, accent=(0,180,255)):
    pct = clamp(pct, 0, 100)
    draw.rectangle([x, y, x+w, y+h], outline=(70,70,70), width=1)
    fillw = int(w * (pct/100.0))
    draw.rectangle([x, y, x+fillw, y+h], fill=grad_pct(pct))
    draw.text((x, y-14), label, font=F_S, fill=(200,200,200))

# ================= Network graph state =================
NET_SAMPLES = 60
net_hist = [0] * NET_SAMPLES  # kbps
_last_net = None
_last_t = None

def update_net_hist():
    global _last_net, _last_t, net_hist
    c = psutil.net_io_counters()
    t = time.time()
    if _last_net is None:
        _last_net = c
        _last_t = t
        return
    dt = max(t - _last_t, 0.001)
    # total bytes delta (rx+tx) to kbps
    dbytes = (c.bytes_recv - _last_net.bytes_recv) + (c.bytes_sent - _last_net.bytes_sent)
    kbps = (dbytes * 8.0 / 1000.0) / dt
    kbps = min(kbps, 9999)
    net_hist = net_hist[1:] + [int(kbps)]
    _last_net = c
    _last_t = t

def draw_net_graph(draw, x, y, w, h, label="NET kbps"):
    # graph background
    draw.rectangle([x, y, x+w, y+h], outline=(70,70,70), width=1)
    draw.text((x, y-14), label, font=F_S, fill=(200,200,200))

    vals = net_hist[-w:] if w < len(net_hist) else net_hist[:]  # use last samples
    vmax = max(max(vals), 1)
    # draw polyline bars (bottom-up)
    for i, v in enumerate(vals[-w:]):
        px = x + i
        ph = int((v / vmax) * (h-2))
        draw.line([px, y+h-2, px, y+h-2-ph], fill=(0, 170, 255))

    # annotate peak
    draw.text((x+w-52, y+2), f"pk {vmax}", font=F_XS, fill=(160,160,160))

# ================= Weather helpers =================
def owm_get_current(api_key: str):
    r = requests.get(
        "https://api.openweathermap.org/data/2.5/weather",
        params={"q": CITY_Q, "appid": api_key, "units": "imperial"},
        timeout=6
    )
    r.raise_for_status()
    return r.json()

def owm_get_forecast(api_key: str):
    # 5 day / 3 hour forecast (free tier friendly)
    r = requests.get(
        "https://api.openweathermap.org/data/2.5/forecast",
        params={"q": CITY_Q, "appid": api_key, "units": "imperial"},
        timeout=6
    )
    r.raise_for_status()
    return r.json()

def weather_accent(weather_id: int, icon: str, main: str):
    # iPhone-ish vibes: cool blues for rain, warm for sun, purple for storms, grey for clouds
    if 200 <= weather_id <= 232:
        return (180, 70, 255)   # thunder
    if 300 <= weather_id <= 531:
        return (0, 160, 255)    # rain
    if 600 <= weather_id <= 622:
        return (140, 220, 255)  # snow
    if 701 <= weather_id <= 781:
        return (160, 160, 180)  # fog
    if main.lower() == "clear":
        return (255, 170, 0)    # sun
    if main.lower() == "clouds":
        return (160, 200, 255)  # clouds
    return (0, 180, 255)

def icon_path(icon_code: str):
    # prefer local (no @2x in filename)
    p = os.path.join(ICON_DIR, f"{icon_code}.png")
    if os.path.exists(p):
        return p
    # allow user to have @2x files; fallback
    p2 = os.path.join(ICON_DIR, f"{icon_code}@2x.png")
    return p2 if os.path.exists(p2) else None

def load_icon(icon_code: str, size=72):
    p = icon_path(icon_code)
    if not p:
        return None
    try:
        img = Image.open(p).convert("RGBA")
        return img.resize((size, size), Image.LANCZOS)
    except:
        return None

def severe_banner_text(wid: int, wind_mph: float, rain_1h: float | None):
    # No OneCall alerts on free endpoint; do best-effort "severe-ish" banner.
    if 200 <= wid <= 232:
        return "ALERT: THUNDERSTORMS"
    if wind_mph >= 25:
        return "ADVISORY: STRONG WIND"
    if rain_1h is not None and rain_1h >= 0.30:
        return "ADVISORY: HEAVY RAIN"
    return None

def parse_hourly_strip(fc_json, tz_offset):
    # Use next 6 forecast points (18 hours) -> (time_label, temp, icon)
    out = []
    lst = fc_json.get("list", [])[:6]
    for it in lst:
        ts = it.get("dt", 0)
        tloc = now_local(ts, tz_offset)
        lbl = tloc.strftime("%-I%p") if hasattr(tloc, "strftime") else "H"
        temp = int(round(it["main"]["temp"]))
        icon = it["weather"][0]["icon"]
        out.append((lbl, temp, icon))
    return out

def compute_tomorrow_hilo(fc_json, tz_offset):
    # Find tomorrow's date in local tz and compute min/max from forecast points that day
    lst = fc_json.get("list", [])
    if not lst:
        return None
    today = now_local(time.time(), tz_offset).date()
    tomorrow = today + datetime.timedelta(days=1)
    temps = []
    for it in lst:
        d = now_local(it.get("dt", 0), tz_offset).date()
        if d == tomorrow:
            temps.append(it["main"]["temp"])
    if not temps:
        return None
    return int(round(max(temps))), int(round(min(temps)))

def draw_sun_arc(draw, x, y, w, h, sunrise_ts, sunset_ts, now_ts, accent):
    # Simple sunrise/sunset arc like iPhone
    # arc along top of a box
    # normalize
    if sunrise_ts <= 0 or sunset_ts <= 0 or sunset_ts <= sunrise_ts:
        return
    t = clamp((now_ts - sunrise_ts) / (sunset_ts - sunrise_ts), 0.0, 1.0)
    # base arc
    bbox = [x, y, x+w, y+h]
    draw.arc(bbox, 180, 360, fill=(70,70,70), width=3)
    # progress arc
    draw.arc(bbox, 180, 180 + int(180*t), fill=accent, width=3)
    # sun dot position
    ang = math.pi * (1.0 - t)
    cx = x + w/2.0
    cy = y + h/2.0
    rx = w/2.0
    ry = h/2.0
    sx = cx + rx*math.cos(ang)
    sy = cy + ry*math.sin(ang)
    draw.ellipse([sx-4, sy-4, sx+4, sy+4], fill=(255, 220, 80))
    # labels
    sr = now_local(sunrise_ts, TZ_OFFSET_FALLBACK).strftime("%-I:%M")
    ss = now_local(sunset_ts, TZ_OFFSET_FALLBACK).strftime("%-I:%M")
    draw.text((x, y+h-10), f"Sunrise {sr}", font=F_XS, fill=(170,170,170))
    tw = draw.textlength(f"Sunset {ss}", font=F_XS)
    draw.text((x+w-int(tw), y+h-10), f"Sunset {ss}", font=F_XS, fill=(170,170,170))

# ================= Pages =================
def header(draw, title, subtitle=None, accent=(0,180,255)):
    # top banner
    draw.rectangle([0,0,WIDTH,32], fill=(10,10,10))
    draw.text((10,6), title, font=F_L, fill=WHITE)
    if subtitle:
        draw.text((10,34), subtitle, font=F_S, fill=(180,180,180))
    # accent underline
    draw.line([0,32,WIDTH,32], fill=accent, width=2)

def footer(draw, left, right=None):
    draw.rectangle([0, HEIGHT-18, WIDTH, HEIGHT], fill=(10,10,10))
    draw.text((10, HEIGHT-16), left, font=F_XS, fill=(170,170,170))
    if right:
        w = draw.textlength(right, font=F_XS)
        draw.text((WIDTH-10-int(w), HEIGHT-16), right, font=F_XS, fill=(170,170,170))

def page_perf():
    img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    d = ImageDraw.Draw(img)

    accent = (0,180,255)
    header(d, "RSDESIGN", "PERF", accent)

    cpu = psutil.cpu_percent(interval=None)
    ram = psutil.virtual_memory().percent

    draw_ring(d, WIDTH//2, 104, 58, 10, cpu, "CPU", accent)
    draw_ring(d, WIDTH//2, 224, 58, 10, ram, "RAM", accent)

    tf = get_temp_f()
    up = get_uptime_hm()
    temp_s = f"{tf:.1f}F" if tf is not None else "N/A"
    footer(d, f"Temp {temp_s}", f"Up {up}")
    return img

def page_status():
    img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    d = ImageDraw.Draw(img)

    accent = (0,180,255)
    header(d, "RSDESIGN", "STATUS", accent)

    mem = psutil.virtual_memory().percent
    disk = psutil.disk_usage("/").percent

    # top text block
    ip = get_ip()
    up = get_uptime_hm()
    tf = get_temp_f()
    temp_s = f"{tf:.1f}F" if tf is not None else "N/A"

    d.text((10, 44), f"IP  {ip}", font=F_M, fill=(220,220,220))
    d.text((10, 66), f"Up  {up}", font=F_M, fill=(220,220,220))
    d.text((10, 88), f"Tmp {temp_s}", font=F_M, fill=(220,220,220))

    # bars
    draw_bar(d, 10, 122, WIDTH-20, 16, mem, "Memory")
    draw_bar(d, 10, 160, WIDTH-20, 16, disk, "Disk")

    # network graph bottom 2/3 (lowered so it doesn't cover text)
    update_net_hist()
    gx, gy = 10, 198
    gw, gh = WIDTH-20, 98
    draw_net_graph(d, gx, gy, gw, gh, "Network (kbps)")

    footer(d, "Ctrl+C to stop")
    return img

def page_weather(anim_phase=0):
    img = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
    d = ImageDraw.Draw(img)

    api = os.getenv("OWM_API_KEY", "").strip()
    if not api:
        header(d, "RSDESIGN", "WEATHER", (200,60,60))
        text_center(d, 90, "Set OWM_API_KEY", F_M, (255,120,120))
        text_center(d, 115, "export OWM_API_KEY=\"...\"", F_XS, (180,180,180))
        footer(d, "Weather unavailable")
        return img

    try:
        cur = owm_get_current(api)
        fc  = owm_get_forecast(api)
    except Exception:
        header(d, "RSDESIGN", "WEATHER", (200,60,60))
        text_center(d, 90, "Weather unavailable", F_M, (255,120,120))
        footer(d, "Check key / network")
        return img

    wid = int(cur["weather"][0]["id"])
    main = cur["weather"][0]["main"]
    desc = cur["weather"][0]["description"].title()
    icon = cur["weather"][0]["icon"]

    tz_offset = int(cur.get("timezone", TZ_OFFSET_FALLBACK))
    accent = weather_accent(wid, icon, main)

    header(d, "RSDESIGN", "WEATHER", accent)

    # City line (bigger, below WEATHER, no overlap)
    d.text((10, 38), "Gig Harbor, WA", font=F_M, fill=(200,200,200))

    temp = int(round(cur["main"]["temp"]))
    feels = int(round(cur["main"]["feels_like"]))
    hum = int(cur["main"]["humidity"])
    wind = float(cur.get("wind", {}).get("speed", 0.0))
    rain_1h = None
    if "rain" in cur and isinstance(cur["rain"], dict):
        rain_1h = cur["rain"].get("1h", None)

    # Severe banner
    banner = severe_banner_text(wid, wind, rain_1h)
    if banner:
        d.rectangle([0, 56, WIDTH, 76], fill=(60,20,20))
        text_center(d, 60, banner, F_S, (255,180,180))
        y0 = 78
    else:
        y0 = 56

    # Icon + temp area (iPhone-ish)
    ico = load_icon(icon, size=76)
    # subtle "breathing" animation only on this page
    dx = int(2 * math.sin(anim_phase * 0.5))
    dy = int(2 * math.cos(anim_phase * 0.35))
    if ico:
        img.paste(ico, (48+dx, y0+10+dy), ico)
    d.text((10, y0+18), f"{temp}F", font=F_XL, fill=WHITE)
    d.text((10, y0+48), desc, font=F_S, fill=(210,210,210))
    d.text((10, y0+66), f"Feels {feels}F", font=F_XS, fill=(170,170,170))

    # Tomorrow hi/lo
    hilo = compute_tomorrow_hilo(fc, tz_offset)
    if hilo:
        hi, lo = hilo
        d.text((10, y0+86), f"Tomorrow  H {hi}F  L {lo}F", font=F_XS, fill=(170,170,170))

    # Hourly strip (bottom-ish)
    strip = parse_hourly_strip(fc, tz_offset)
    sx, sy = 6, 170
    d.rectangle([0, sy-8, WIDTH, sy+76], fill=(8,8,8))
    d.line([0, sy-8, WIDTH, sy-8], fill=accent, width=2)

    # 6 columns
    colw = WIDTH // 6
    for i, (lbl, tval, ic) in enumerate(strip):
        x = i*colw
        text_center(d, sy-4, lbl, F_XS, (200,200,200), x, x+colw)
        ico2 = load_icon(ic, size=28)
        if ico2:
            img.paste(ico2, (x + (colw-28)//2, sy+10), ico2)
        text_center(d, sy+44, f"{tval}F", F_XS, WHITE, x, x+colw)

    # Details block + sunrise/sunset arc at very bottom (no overlap with footer)
    det_y = 252
    d.text((10, det_y), f"Wind {int(round(wind))} mph", font=F_XS, fill=(170,170,170))
    d.text((10, det_y+14), f"Humidity {hum}%", font=F_XS, fill=(170,170,170))

    sr = int(cur.get("sys", {}).get("sunrise", 0))
    ss = int(cur.get("sys", {}).get("sunset", 0))
    draw_sun_arc(d, 86, 246, 78, 36, sr, ss, int(time.time()), accent)

    footer(d, "OpenWeather", now_local(time.time(), tz_offset).strftime("%a %I:%M%p").lstrip("0"))
    return img

# ================= Main loop =================
def main():
    init_display()

    # prime psutil
    psutil.cpu_percent(interval=None)
    update_net_hist()

    pages = [
        ("PERF", page_perf),
        ("STATUS", page_status),
        ("WEATHER", page_weather),
    ]

    p = 0
    while True:
        name, fn = pages[p]

        if name == "WEATHER":
            # animate icon for PAGE_TIME seconds at low fps (should not flicker)
            frames = max(1, int(PAGE_TIME * WEATHER_FRAME_HZ))
            for k in range(frames):
                img = fn(anim_phase=k)
                push(img)
                time.sleep(1.0 / WEATHER_FRAME_HZ)
        else:
            img = fn()
            push(img)
            time.sleep(PAGE_TIME)

        p = (p + 1) % len(pages)

if __name__ == "__main__":
    main()
