"""
Void Breaker
============

A portrait-orientation sci-fi space shooter built with Python and Pygame,
following the same engineering pattern as Neon Breakout: procedurally
generated audio (no external assets), a resizable / fullscreen-safe
display, persistent config + high scores, a full button-driven menu, a
pause system, and a crash-hardened main loop.

10 hand-crafted stages, each ending in a unique boss with its own attack
pattern. Clearing every stage unlocks Stage Select and Endless Mode.

Controls
--------
    Move ship          WASD / Arrow keys, or move the mouse
    Fire blaster        Hold SPACE or hold Left Mouse Button
    Fire missile        Tap LEFT SHIFT or Right Mouse Button (5s cooldown)
    Pause               P, ESC, or click the pause icon (top-left)
    Show/hide mouse     Ctrl or Alt
    Restart             R (on Game Over / Victory screen)
    Mute (quick)        M
    Quit                ESC (from main menu) or close window

Requirements
------------
    Python 3.9+, pygame, numpy
"""

import os

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")  # must be set before `import pygame`

import json
import math
import random
import sys
import uuid

import numpy as np
import pygame
import pygame.gfxdraw

# --------------------------------------------------------------------------
# DEBUGGING CONFIGURATION
# --------------------------------------------------------------------------
DEBUG = True  # Set to False to disable console debug print statements


def log(msg):
    """Helper function to print debug messages only when DEBUG is enabled.
    Wrapped defensively: when launched as a .pyw (pythonw.exe), there is no
    console at all and sys.stdout/stderr can be None, which would otherwise
    make ANY print() call crash the game before a window ever appears."""
    if DEBUG:
        try:
            print(f"[DEBUG] {msg}")
        except Exception:
            pass


# --------------------------------------------------------------------------
# PERSISTENCE (config + high scores)
# Loaded before the window is created so the saved fullscreen preference
# applies on startup. Stored in the OS's per-user app data folder (not next
# to the script, which may not be writable once packaged).
# --------------------------------------------------------------------------
APP_NAME = "VoidBreaker"


def _app_data_dir():
    try:
        if sys.platform.startswith("win"):
            base = (
                os.environ.get("LOCALAPPDATA")
                or os.environ.get("APPDATA")
                or os.path.expanduser("~")
            )
        elif sys.platform == "darwin":
            base = os.path.expanduser("~/Library/Application Support")
        else:
            base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        path = os.path.join(base, APP_NAME)
        os.makedirs(path, exist_ok=True)
        return path
    except OSError:
        return (
            os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() else os.getcwd()
        )


BASE_DIR = _app_data_dir()
CONFIG_FILE = os.path.join(BASE_DIR, "void_breaker_config.json")
HIGHSCORE_FILE = os.path.join(BASE_DIR, "void_breaker_scores.json")
MAX_HIGHSCORES = 10

DEFAULT_CONFIG = {
    "max_stage_reached": 1,
    "game_completed": False,
    "bgm_on": True,
    "sfx_on": True,
    "fullscreen": False,
    # Remembered windowed (non-fullscreen) geometry so the player doesn't have
    # to re-drag/resize the window every launch. window_x/y stay None until
    # the player has actually moved the window at least once.
    "window_w": 480,
    "window_h": 854,
    "window_x": None,
    "window_y": None,
}


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_CONFIG:
                if k in data:
                    cfg[k] = data[k]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return cfg


def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(cfg, f, indent=2)
    except OSError as e:
        log(f"Failed to save config: {e}")


def load_highscores():
    try:
        with open(HIGHSCORE_FILE, "r") as f:
            data = json.load(f)
        if isinstance(data, list):
            cleaned = []
            for e in data:
                if isinstance(e, dict) and "score" in e and "stage" in e:
                    e.setdefault("endless", False)  # older saves predate this field
                    cleaned.append(e)
            cleaned.sort(key=lambda e: e["score"], reverse=True)
            return cleaned[:MAX_HIGHSCORES]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return []


def save_highscore(score, stage, endless=False, run_id=None):
    """Append/update a score, keep the top MAX_HIGHSCORES, write to disk. Returns the
    updated list. `endless` is stored explicitly rather than inferred from the stage
    number, since endless mode's first loop (endless_loop=0) reports stage==NUM_STAGES
    too - the same value a normal Stage 10 clear would have. When `run_id` is given,
    any existing entry for that same run is replaced rather than duplicated - this lets
    us persist the score continuously while a run is still in progress (see
    Game._autosave_tick) without spamming the list with one entry per autosave tick."""
    scores = load_highscores()
    if run_id is not None:
        scores = [e for e in scores if e.get("run_id") != run_id]
    entry = {"score": score, "stage": stage, "endless": endless}
    if run_id is not None:
        entry["run_id"] = run_id
    scores.append(entry)
    scores.sort(key=lambda e: e["score"], reverse=True)
    scores = scores[:MAX_HIGHSCORES]
    try:
        with open(HIGHSCORE_FILE, "w") as f:
            json.dump(scores, f, indent=2)
        log(f"High scores saved to {HIGHSCORE_FILE}")
    except OSError as e:
        log(f"Failed to save high scores: {e}")
    return scores


CONFIG = load_config()

# --------------------------------------------------------------------------
# WINDOWS DPI AWARENESS - must happen before pygame touches the display
# --------------------------------------------------------------------------
if sys.platform.startswith("win"):
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception as e:
        log(f"Could not set Windows DPI awareness: {e}")

# --------------------------------------------------------------------------
# SETUP
# --------------------------------------------------------------------------
pygame.init()

SOUND_ENABLED = True
try:
    pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=512)
except pygame.error:
    SOUND_ENABLED = False

SFX_ON = bool(CONFIG.get("sfx_on", True))
BGM_ON = bool(CONFIG.get("bgm_on", True))

# --------------------------------------------------------------------------
# RESOLUTION (fixed portrait logical canvas)
# --------------------------------------------------------------------------
# Unlike a landscape game, a portrait shooter must never be stretched to fill
# a landscape monitor - that would squash everything sideways. Instead the
# logical canvas is a FIXED portrait size, and present() below always scales
# it to fit inside whatever window/monitor shape exists while preserving
# aspect ratio, letterboxing (bars on the sides) or pillarboxing (bars on
# top/bottom) as needed. This also sidesteps a whole class of resize-timing
# bugs the adaptive-resolution approach would otherwise need to guard against.
DESIGN_W, DESIGN_H = 720, 1280
WIDTH, HEIGHT = DESIGN_W, DESIGN_H
MIN_WINDOW_W, MIN_WINDOW_H = 320, 400

screen = pygame.Surface((WIDTH, HEIGHT))  # everything is drawn onto this fixed-size surface
window = None  # the real OS window - created by apply_fullscreen() below


def _native_desktop_size():
    """Get the monitor's real desktop resolution."""
    try:
        sizes = pygame.display.get_desktop_sizes()
        if sizes:
            return sizes[0]
    except Exception as e:
        log(f"get_desktop_sizes() failed: {e}")
    try:
        info = pygame.display.Info()
        return (info.current_w, info.current_h)
    except Exception:
        return (1280, 720)


def _clamped_windowed_size():
    w = max(MIN_WINDOW_W, CONFIG.get("window_w") or 480)
    h = max(MIN_WINDOW_H, CONFIG.get("window_h") or 854)
    return w, h


def _set_window_pos_hint(x, y):
    if x is None or y is None:
        os.environ.pop("SDL_VIDEO_WINDOW_POS", None)
    else:
        os.environ["SDL_VIDEO_WINDOW_POS"] = f"{int(x)},{int(y)}"


def _clamped_saved_position(w, h):
    x, y = CONFIG.get("window_x"), CONFIG.get("window_y")
    if x is None or y is None:
        return None, None
    dw, dh = _native_desktop_size()
    x = min(max(0, x), max(0, dw - w))
    y = min(max(0, y), max(0, dh - h))
    return x, y


_last_fullscreen_toggle_time = 0.0
_FULLSCREEN_DEBOUNCE = 0.35
_suppress_resize_until = 0.0


def set_cursor_locked(locked):
    """Hide+confine (locked=True) or show+free (locked=False) the OS cursor together."""
    pygame.mouse.set_visible(not locked)
    pygame.event.set_grab(locked)


def apply_fullscreen(on):
    """Switch between a resizable window (restored to its last size/position) and
    a borderless fullscreen window sized to the monitor's native resolution.

    Fully tears down and recreates the display subsystem before every switch so
    set_mode() always creates a genuinely new window; every step is wrapped so a
    failure can never leave `window` as None."""
    global window

    try:
        pygame.display.quit()
        pygame.display.init()

        if on:
            size = _native_desktop_size()
            _set_window_pos_hint(0, 0)
            window = pygame.display.set_mode(size, pygame.NOFRAME)
        else:
            w, h = _clamped_windowed_size()
            x, y = _clamped_saved_position(w, h)
            _set_window_pos_hint(x, y)
            window = pygame.display.set_mode((w, h), pygame.RESIZABLE)

        pygame.display.set_caption("VOID BREAKER")
        try:
            pygame.display.set_icon(build_app_icon())
        except Exception as e:
            log(f"Could not reset window icon after display re-init: {e}")
    except Exception as e:
        log(f"apply_fullscreen({on}) failed entirely ({e}); forcing a safe windowed fallback.")
        try:
            window = pygame.display.set_mode((480, 854), pygame.RESIZABLE)
        except Exception as e2:
            log(f"Even the safe windowed fallback failed: {e2}")

    if window is not None:
        global _suppress_resize_until
        _suppress_resize_until = pygame.time.get_ticks() / 1000.0 + 0.5
        log(f"Fullscreen set to {on}. Window size: {window.get_size()}")


def toggle_fullscreen():
    global _last_fullscreen_toggle_time
    now = pygame.time.get_ticks() / 1000.0
    if now - _last_fullscreen_toggle_time < _FULLSCREEN_DEBOUNCE:
        log("Fullscreen toggle ignored (debounced).")
        return
    _last_fullscreen_toggle_time = now
    CONFIG["fullscreen"] = not CONFIG["fullscreen"]
    apply_fullscreen(CONFIG["fullscreen"])
    save_config(CONFIG)


def build_app_icon(size=64):
    """Procedurally draw a sci-fi ship icon - zero external image files needed."""
    icon = pygame.Surface((size, size), pygame.SRCALPHA)
    icon.fill((6, 8, 20, 255))
    pts = [
        (size * 0.5, size * 0.12),
        (size * 0.78, size * 0.82),
        (size * 0.5, size * 0.66),
        (size * 0.22, size * 0.82),
    ]
    pygame.draw.polygon(icon, (80, 220, 255), pts)
    pygame.draw.circle(icon, (255, 90, 90), (int(size * 0.5), int(size * 0.4)), max(2, int(size * 0.06)))
    return icon


apply_fullscreen(CONFIG.get("fullscreen", False))
try:
    pygame.display.set_icon(build_app_icon())
except Exception as e:
    log(f"Could not set window icon: {e}")

clock = pygame.time.Clock()
FPS = 60

FONT_BIG = pygame.font.SysFont("consolas", 56, bold=True)
FONT_MED = pygame.font.SysFont("consolas", 30, bold=True)
FONT_SMALL = pygame.font.SysFont("consolas", 18, bold=True)
FONT_TINY = pygame.font.SysFont("consolas", 14)

# ---- Sci-fi color palette -----------------------------------------------
BG_COLOR = (4, 6, 16)
BG_COLOR2 = (8, 10, 26)
STAR_COLOR = (200, 210, 255)

CYAN = (70, 220, 255)
TEAL = (60, 255, 210)
MAGENTA = (255, 80, 210)
YELLOW = (255, 230, 90)
GREEN = (100, 255, 150)
ORANGE = (255, 160, 60)
RED = (255, 70, 90)
PURPLE = (160, 100, 255)
BLUE = (90, 150, 255)
WHITE = (235, 240, 255)
GRAY = (120, 128, 145)
ALERT_RED = (255, 40, 60)

POWERUP_INFO = {
    # key: (letter, color, rarity_weight)
    "triple": ("3", CYAN, 5),
    "quint": ("5", MAGENTA, 2),
    "missile_overcharge": ("M", ORANGE, 4),
    "shield": ("O", BLUE, 4),
    "life": ("+", WHITE, 3),
}

# --------------------------------------------------------------------------
# PROCEDURAL AUDIO - synthesized SFX + an ambient looping pad track.
# No external audio assets required.
# --------------------------------------------------------------------------
SAMPLE_RATE = 44100


def midi_to_freq(m):
    return 440.0 * (2.0 ** ((m - 69) / 12.0))


def _note(freq, dur, wave="square", vol=0.16, attack=0.006, release=0.03):
    n = max(1, int(SAMPLE_RATE * dur))
    t = np.linspace(0, dur, n, False)
    if wave == "square":
        data = np.sign(np.sin(2 * np.pi * freq * t))
    elif wave == "saw":
        data = 2 * (t * freq - np.floor(0.5 + t * freq))
    elif wave == "tri":
        data = 2 * np.abs(2 * (t * freq - np.floor(t * freq + 0.5))) - 1
    else:
        data = np.sin(2 * np.pi * freq * t)
    a = min(n, max(1, int(n * attack / dur))) if dur > 0 else 1
    r = min(max(0, n - a), max(1, int(n * release / dur))) if dur > 0 else 0
    env = np.ones(n)
    env[:a] = np.linspace(0, 1, a)
    if r > 0:
        env[-r:] = np.linspace(1, 0, r)
    return data * env * vol


def _noise_burst(vol=0.2, dur=0.12, decay=10):
    n = int(SAMPLE_RATE * dur)
    data = np.random.uniform(-1, 1, n)
    amp = np.exp(-np.linspace(0, 1, n) * decay)
    return data * amp * vol


def _sweep(f0, f1, dur, vol=0.2, wave="square"):
    n = int(SAMPLE_RATE * dur)
    t = np.linspace(0, dur, n, False)
    freq_env = np.linspace(f0, f1, n)
    phase = np.cumsum(2 * np.pi * freq_env / SAMPLE_RATE)
    if wave == "square":
        data = np.sign(np.sin(phase))
    else:
        data = np.sin(phase)
    amp = np.exp(-np.linspace(0, 1, n) * 6)
    return data * amp * vol


def _sum_arrays(*arrays):
    """Sum 1-D arrays of possibly different lengths by zero-padding to the longest."""
    n = max(len(a) for a in arrays)
    out = np.zeros(n)
    for a in arrays:
        out[: len(a)] += a
    return out


def _to_sound(mono):
    mono = np.clip(mono, -1, 1)
    stereo = np.column_stack([mono, mono])
    arr = (stereo * 32767).astype(np.int16)
    return pygame.sndarray.make_sound(np.ascontiguousarray(arr))


def _build_sounds():
    s = {}
    s["laser"] = _to_sound(_sweep(1200, 500, 0.09, vol=0.14, wave="square"))
    s["missile"] = _to_sound(_sum_arrays(_sweep(300, 900, 0.22, vol=0.18, wave="saw"), _noise_burst(0.05, 0.22, 4)))
    s["explosion"] = _to_sound(_sum_arrays(_noise_burst(0.32, 0.35, 5), _sweep(220, 40, 0.3, vol=0.2)))
    s["explosion_big"] = _to_sound(_sum_arrays(_noise_burst(0.4, 0.6, 3), _sweep(180, 30, 0.55, vol=0.28)))
    s["hit"] = _to_sound(_noise_burst(0.18, 0.08, 14))
    s["player_hit"] = _to_sound(_sum_arrays(_sweep(500, 120, 0.25, vol=0.24, wave="saw"), _noise_burst(0.15, 0.2, 6)))
    s["powerup"] = _to_sound(
        np.concatenate(
            [_note(midi_to_freq(m), 0.07, "square", 0.16, 0.004, 0.02) for m in (64, 68, 71, 76)]
        )
    )
    s["shield"] = _to_sound(_note(midi_to_freq(60), 0.3, "sine", 0.16, 0.02, 0.2))
    s["ui_click"] = _to_sound(_note(880, 0.05, "square", 0.14, 0.003, 0.02))
    s["boss_alert"] = _to_sound(
        np.concatenate(
            [
                _sweep(200, 700, 0.18, vol=0.22, wave="saw"),
                np.zeros(int(SAMPLE_RATE * 0.05)),
                _sweep(200, 700, 0.18, vol=0.22, wave="saw"),
            ]
        )
    )
    s["life_lost"] = _to_sound(_sweep(400, 90, 0.4, vol=0.22, wave="tri"))
    s["stage_clear"] = _to_sound(
        np.concatenate(
            [_note(midi_to_freq(m), 0.12, "tri", 0.18, 0.005, 0.05) for m in (60, 64, 67, 72, 76)]
        )
    )
    s["countdown_tick"] = _to_sound(_note(660, 0.09, "square", 0.16, 0.004, 0.03))
    s["countdown_go"] = _to_sound(_note(midi_to_freq(76), 0.16, "square", 0.2, 0.004, 0.05))
    return s


def _build_ambient_track():
    """A dark sci-fi ambient bed, ~2.8 minutes long before it loops. Chords are
    voiced without a 3rd (root/5th/9th/octave) - that reads as spacious and
    mysterious rather than sad, since it's neither major nor minor. A soft
    sub-bass pulse, a gentle arpeggio motif, and sparse high shimmer pings
    give it some life so it doesn't sit as a flat, depressing drone."""
    bar_dur = 3.6
    section_a = [38, 41, 43, 40]  # D2 F2 G2 E2 - dorian-ish, open/mysterious
    section_b = [43, 45, 41, 38]  # G2 A2 F2 D2
    roots = (section_a * 3 + section_b * 3) * 2  # 48 bars =~ 172.8s
    bars = len(roots)
    total = int(bar_dur * bars * SAMPLE_RATE)
    buf = np.zeros(total + SAMPLE_RATE)

    voicing_intervals = (0, 7, 14, 19)  # root, 5th, 9th(+octave), 5th+octave - no 3rd
    t_global = 0.0
    for i, root in enumerate(roots):
        start = int(i * bar_dur * SAMPLE_RATE)

        # --- pad chord (open voicing, slow swell) ---
        pad = sum(_note(midi_to_freq(root + iv), bar_dur * 0.98, "sine", 0.06, 0.9, 1.3) for iv in voicing_intervals)
        if i % 4 == 3:  # occasional gentle 6th for warmth, kept rare so it stays subtle
            pad = pad + _note(midi_to_freq(root + 9), bar_dur * 0.98, "sine", 0.035, 1.0, 1.2)
        # slow tremolo so the pad breathes instead of sitting static
        lfo_t = np.linspace(t_global, t_global + bar_dur, len(pad), False)
        pad = pad * (0.82 + 0.18 * np.sin(2 * np.pi * 0.065 * lfo_t))
        end = min(total, start + len(pad))
        buf[start:end] += pad[: end - start]

        # --- soft sub-bass pulse: a slow two-beat "heartbeat" per bar ---
        for beat_t in (0.0, bar_dur * 0.52):
            sub = _note(midi_to_freq(root - 12), 0.5, "sine", 0.11, 0.02, 0.4)
            s0 = start + int(beat_t * SAMPLE_RATE)
            e0 = min(total, s0 + len(sub))
            if s0 < total:
                buf[s0:e0] += sub[: e0 - s0]

        # --- gentle arpeggio motif for forward motion ---
        arp_notes = (root + 12, root + 19, root + 14, root + 19)
        step = bar_dur / 8
        for k in range(8):
            note = arp_notes[k % len(arp_notes)]
            p = _note(midi_to_freq(note), step * 0.85, "tri", 0.045, 0.01, step * 0.7)
            s0 = start + int(k * step * SAMPLE_RATE)
            e0 = min(total, s0 + len(p))
            if s0 < total:
                buf[s0:e0] += p[: e0 - s0]

        # --- sparse high shimmer ping, roughly every third bar ---
        if i % 3 == 1:
            ping = _note(midi_to_freq(root + 24), 1.4, "sine", 0.05, 0.05, 1.2)
            s0 = start + int(bar_dur * 0.4 * SAMPLE_RATE)
            e0 = min(total, s0 + len(ping))
            if s0 < total:
                buf[s0:e0] += ping[: e0 - s0]

        t_global += bar_dur

    return _to_sound(buf[:total])


SOUNDS = {}
MUSIC_CHANNEL = None
if SOUND_ENABLED:
    try:
        SOUNDS = _build_sounds()
        music_sound = _build_ambient_track()
        MUSIC_CHANNEL = music_sound.play(loops=-1)
        if MUSIC_CHANNEL:
            MUSIC_CHANNEL.set_volume(0.55 if BGM_ON else 0.0)
    except Exception as e:
        SOUND_ENABLED = False
        log(f"Sound engine failed to initialize: {e}")


def play_sound(name):
    if not SOUND_ENABLED or not SFX_ON:
        return
    snd = SOUNDS.get(name)
    if snd:
        snd.play()


def set_sfx(on):
    global SFX_ON
    SFX_ON = on
    CONFIG["sfx_on"] = on
    save_config(CONFIG)
    log(f"SFX set to {on}")


def set_bgm(on):
    global BGM_ON
    BGM_ON = on
    CONFIG["bgm_on"] = on
    if MUSIC_CHANNEL:
        MUSIC_CHANNEL.set_volume(0.55 if on else 0.0)
    save_config(CONFIG)
    log(f"BGM set to {on}")


def quick_toggle_mute():
    turn_on = not (SFX_ON or BGM_ON)
    set_sfx(turn_on)
    set_bgm(turn_on)


# --------------------------------------------------------------------------
# PRESENT - aspect-preserving letterbox/pillarbox scale of the fixed
# portrait logical canvas into whatever window/monitor shape exists.
# --------------------------------------------------------------------------
def present(shake_offset=(0, 0)):
    win_w, win_h = window.get_size()
    if win_w <= 0 or win_h <= 0:
        return  # window is mid-transition (e.g. toggling fullscreen) - skip this frame
    window.fill((0, 0, 0))
    scale = min(win_w / WIDTH, win_h / HEIGHT)
    scale = max(scale, 0.0001)
    out_w, out_h = max(1, int(WIDTH * scale)), max(1, int(HEIGHT * scale))
    try:
        scaled = pygame.transform.smoothscale(screen, (out_w, out_h))
    except Exception as e:
        log(f"smoothscale failed ({e}); skipping frame.")
        return
    x = (win_w - out_w) // 2 + shake_offset[0]
    y = (win_h - out_h) // 2 + shake_offset[1]
    window.blit(scaled, (x, y))
    pygame.display.flip()


def window_to_logical(raw_pos):
    """Map a raw OS-window mouse position into logical canvas coordinates,
    accounting for the letterbox/pillarbox offset and scale. Always clamped
    to the canvas so a mouse sitting in the bars can never crash anything."""
    win_w, win_h = window.get_size()
    if win_w <= 0 or win_h <= 0:
        return (WIDTH / 2, HEIGHT / 2)
    scale = min(win_w / WIDTH, win_h / HEIGHT)
    scale = max(scale, 0.0001)
    out_w, out_h = WIDTH * scale, HEIGHT * scale
    off_x = (win_w - out_w) / 2
    off_y = (win_h - out_h) / 2
    lx = (raw_pos[0] - off_x) / scale
    ly = (raw_pos[1] - off_y) / scale
    lx = max(0.0, min(WIDTH, lx))
    ly = max(0.0, min(HEIGHT, ly))
    return (lx, ly)


# --------------------------------------------------------------------------
# GLOW HELPERS
# --------------------------------------------------------------------------
def draw_aa_circle(surface, color, pos, radius):
    x, y = int(round(pos[0])), int(round(pos[1]))
    r = max(1, int(round(radius)))
    pygame.gfxdraw.filled_circle(surface, x, y, r, color)
    pygame.gfxdraw.aacircle(surface, x, y, r, color)


def draw_glow_rect(surface, rect, color, layers=5, expand=3, base_alpha=55, radius=5):
    pad = layers * expand
    glow = pygame.Surface((rect.width + pad * 2, rect.height + pad * 2), pygame.SRCALPHA)
    cx, cy = glow.get_width() // 2, glow.get_height() // 2
    for i in range(layers, 0, -1):
        alpha = int(base_alpha * (i / layers))
        w = rect.width + i * expand * 2
        h = rect.height + i * expand * 2
        r = pygame.Rect(0, 0, w, h)
        r.center = (cx, cy)
        pygame.draw.rect(glow, (*color, alpha), r, border_radius=radius + i)
    surface.blit(glow, (rect.centerx - cx, rect.centery - cy))
    pygame.draw.rect(surface, color, rect, border_radius=radius)


def draw_glow_circle(surface, pos, radius, color, layers=4, expand=3, base_alpha=70):
    pad = layers * expand
    size = int((radius + pad) * 2)
    glow = pygame.Surface((size, size), pygame.SRCALPHA)
    c = size // 2
    for i in range(layers, 0, -1):
        alpha = int(base_alpha * (i / layers))
        draw_aa_circle(glow, (*color, alpha), (c, c), radius + i * expand)
    surface.blit(glow, (pos[0] - c, pos[1] - c))
    draw_aa_circle(surface, color, pos, radius)


def draw_glow_poly(surface, points, color, layers=4, expand=2, base_alpha=60):
    """Soft glow behind an arbitrary polygon (used for ships). Sized to the
    polygon's own bounding box (with padding) rather than the whole screen -
    allocating a full-screen SRCALPHA surface per call (as before) is the
    single biggest per-frame cost once several ships are on screen at once."""
    cx = sum(p[0] for p in points) / len(points)
    cy = sum(p[1] for p in points) / len(points)
    max_dist = max(math.hypot(px - cx, py - cy) for px, py in points)
    pad = int(max_dist + layers * expand + 4)
    size = pad * 2
    ox, oy = cx - pad, cy - pad
    glow = pygame.Surface((size, size), pygame.SRCALPHA)
    for i in range(layers, 0, -1):
        alpha = int(base_alpha * (i / layers))
        expanded = []
        for px, py in points:
            dx, dy = px - cx, py - cy
            dist = math.hypot(dx, dy) or 1
            f = (dist + i * expand) / dist
            expanded.append((cx + dx * f - ox, cy + dy * f - oy))
        pygame.draw.polygon(glow, (*color, alpha), expanded)
    surface.blit(glow, (ox, oy))
    pygame.draw.polygon(surface, color, points)


def draw_text_center(surface, text, font, color, center, glow=None):
    if glow:
        glow_surf = font.render(text, True, glow)
        for dx, dy in [(-2, 0), (2, 0), (0, -2), (0, 2)]:
            r = glow_surf.get_rect(center=(center[0] + dx, center[1] + dy))
            surface.blit(glow_surf, r)
    surf = font.render(text, True, color)
    rect = surf.get_rect(center=center)
    surface.blit(surf, rect)


def _heartbeat_pulse(t):
    """0..1 intensity tracing a two-beat 'lub-dub' heartbeat rhythm on a ~1s cycle."""
    cycle = t % 1.0

    def bump(center, width):
        d = abs(cycle - center)
        return max(0.0, 1.0 - d / width)

    return min(1.0, max(bump(0.05, 0.09), bump(0.28, 0.09) * 0.7))


def heartbeat_color(t, dim=(90, 20, 25), bright=(255, 90, 100)):
    """Lerp between a dim and bright red following _heartbeat_pulse - used to make the
    LIVES label blink like a heartbeat monitor when the player is down to their last chance."""
    k = _heartbeat_pulse(t)
    return tuple(int(dim[i] + (bright[i] - dim[i]) * k) for i in range(3))


def flash_color(base_color, hit_flash):
    """Blend toward white-hot red while hit_flash counts down from ~0.15."""
    if hit_flash <= 0:
        return base_color
    t = min(1.0, hit_flash / 0.15)
    r = int(base_color[0] + (255 - base_color[0]) * t)
    g = int(base_color[1] * (1 - t) + 60 * t)
    b = int(base_color[2] * (1 - t) + 60 * t)
    return (min(255, r), max(0, g), max(0, b))


# --------------------------------------------------------------------------
# STARFIELD BACKGROUND (layered parallax + drifting nebula clouds)
# --------------------------------------------------------------------------
# Three depth layers: far stars are small/dim/slow, near stars are
# big/bright/fast. Grouping into explicit layers (rather than one flat
# random range) is what actually reads as parallax depth instead of just
# "twinkly noise."
STAR_LAYERS = [
    # (count, speed_range, size_choices, brightness_range, tint)
    (50, (16, 44), (1,), (0.15, 0.35), (150, 160, 215)),
    (32, (48, 108), (1, 1, 2), (0.35, 0.6), (180, 195, 245)),
    (16, (130, 240), (2, 2, 3), (0.65, 1.0), (215, 228, 255)),
]


class Star:
    __slots__ = ("x", "y", "speed", "size", "b", "tint")

    def __init__(self, speed_range, size_choices, b_range, tint):
        self.x = random.uniform(0, WIDTH)
        self.y = random.uniform(0, HEIGHT)
        self.speed = random.uniform(*speed_range)
        self.size = random.choice(size_choices)
        self.b = random.uniform(*b_range)
        self.tint = tint

    def update(self, dt, speed_mult=1.0):
        self.y += self.speed * dt * speed_mult
        if self.y > HEIGHT:
            self.y = -2
            self.x = random.uniform(0, WIDTH)

    def draw(self, surface):
        c = self.b
        col = (int(self.tint[0] * c), int(self.tint[1] * c), min(255, int(self.tint[2] * c)))
        pygame.draw.rect(surface, col, (int(self.x), int(self.y), self.size, self.size))


STARFIELD = [
    Star(speed_range, size_choices, b_range, tint)
    for (count, speed_range, size_choices, b_range, tint) in STAR_LAYERS
    for _ in range(count)
]

# ---- drifting nebula clouds: soft, very slow, deep-background colored haze ----
NEBULA_COLORS = [(70, 40, 130), (30, 75, 120), (95, 30, 95), (25, 55, 100)]


def _make_nebula_surface(radius, color):
    """Pre-rendered soft radial blob (concentric fading circles). Built once
    at import time so drawing it per-frame is a single blit, not a redraw."""
    size = radius * 2
    surf = pygame.Surface((size, size), pygame.SRCALPHA)
    for i in range(radius, 0, -3):
        alpha = max(1, int(16 * (i / radius)))
        pygame.gfxdraw.filled_circle(surf, radius, radius, i, (*color, alpha))
    return surf


class NebulaBlob:
    __slots__ = ("x", "y", "speed", "radius", "surf")

    def __init__(self):
        self.radius = random.randint(150, 300)
        self.surf = _make_nebula_surface(self.radius, random.choice(NEBULA_COLORS))
        self.x = random.uniform(-60, WIDTH + 60)
        self.y = random.uniform(-HEIGHT, HEIGHT)
        self.speed = random.uniform(4, 12)  # far slower than stars - reads as deep background

    def update(self, dt, speed_mult=1.0):
        self.y += self.speed * dt * speed_mult
        if self.y - self.radius > HEIGHT:
            self.y = -self.radius
            self.x = random.uniform(-60, WIDTH + 60)

    def draw(self, surface):
        surface.blit(self.surf, (self.x - self.radius, self.y - self.radius), special_flags=pygame.BLEND_RGBA_ADD)


NEBULAE = [NebulaBlob() for _ in range(5)]


def update_draw_starfield(surface, dt, speed_mult=1.0):
    surface.fill(BG_COLOR)
    for n in NEBULAE:
        n.update(dt, speed_mult * 0.5)
        n.draw(surface)
    for s in STARFIELD:
        s.update(dt, speed_mult)
        s.draw(surface)


# --------------------------------------------------------------------------
# UI BUTTONS (menu, pause, high scores, stage select)
# --------------------------------------------------------------------------
class Button:
    def __init__(self, rect, label, enabled=True):
        self.rect = pygame.Rect(rect)
        self.label = label
        self.enabled = enabled

    def hit(self, pos):
        return self.enabled and self.rect.collidepoint(pos)

    def draw(self, surface, hovered=False):
        color = CYAN if self.enabled else GRAY
        alpha_layers = 4 if hovered else 3
        draw_glow_rect(surface, self.rect, color, layers=alpha_layers, expand=2, base_alpha=50 if self.enabled else 20, radius=8)
        text_color = (10, 12, 20) if self.enabled else (60, 62, 70)
        draw_text_center(surface, self.label, FONT_SMALL, text_color, self.rect.center)


# --------------------------------------------------------------------------
# PARTICLES
# --------------------------------------------------------------------------
class Particle:
    __slots__ = ("x", "y", "vx", "vy", "life", "max_life", "color", "size")

    def __init__(self, x, y, vx, vy, life, color, size=3):
        self.x, self.y = x, y
        self.vx, self.vy = vx, vy
        self.life = life
        self.max_life = life
        self.color = color
        self.size = size

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vx *= 0.96
        self.vy *= 0.96
        self.life -= dt
        return self.life > 0

    def draw(self, surface):
        t = max(0.0, self.life / self.max_life)
        alpha = int(255 * t)
        size = max(1, int(self.size * t))
        s = pygame.Surface((size * 2, size * 2), pygame.SRCALPHA)
        draw_aa_circle(s, (*self.color, alpha), (size, size), size)
        surface.blit(s, (self.x - size, self.y - size))


def spawn_burst(particles, x, y, color, count=14, speed=(60, 220), life=(0.25, 0.6), size=3):
    for _ in range(count):
        ang = random.uniform(0, math.tau)
        spd = random.uniform(*speed)
        particles.append(
            Particle(x, y, math.cos(ang) * spd, math.sin(ang) * spd, random.uniform(*life), color, size)
        )


class Shockwave:
    """Expanding, fading ring used to give big explosions (boss kills, player
    death) a sense of impact weight beyond just a particle burst."""

    __slots__ = ("x", "y", "life", "max_life", "color", "max_radius")

    def __init__(self, x, y, color, max_radius=90, life=0.4):
        self.x, self.y = x, y
        self.color = color
        self.max_radius = max_radius
        self.life = life
        self.max_life = life

    def update(self, dt):
        self.life -= dt
        return self.life > 0

    def draw(self, surface):
        t = 1.0 - max(0.0, self.life / self.max_life)
        radius = int(self.max_radius * t)
        alpha = int(200 * (1 - t))
        if radius <= 1 or alpha <= 0:
            return
        pad = 4
        s = pygame.Surface((radius * 2 + pad * 2, radius * 2 + pad * 2), pygame.SRCALPHA)
        c = radius + pad
        pygame.gfxdraw.aacircle(s, c, c, radius, (*self.color, alpha))
        pygame.gfxdraw.aacircle(s, c, c, max(0, radius - 3), (*self.color, alpha // 2))
        surface.blit(s, (self.x - c, self.y - c), special_flags=pygame.BLEND_RGBA_ADD)


class FloatingText:
    """Small text that drifts upward and fades - used for score/status popups
    (kill scores, boss bounty, powerup pickups)."""

    __slots__ = ("x", "y", "vy", "life", "max_life", "text", "color", "font")

    def __init__(self, x, y, text, color, life=0.8, font=None):
        self.x, self.y = x, y
        self.vy = -46.0
        self.life = life
        self.max_life = life
        self.text = text
        self.color = color
        self.font = font or FONT_TINY

    def update(self, dt):
        self.y += self.vy * dt
        self.vy *= 0.92
        self.life -= dt
        return self.life > 0

    def draw(self, surface):
        t = max(0.0, self.life / self.max_life)
        alpha = int(255 * min(1.0, t * 1.6))
        surf = self.font.render(self.text, True, self.color)
        surf.set_alpha(alpha)
        r = surf.get_rect(center=(int(self.x), int(self.y)))
        surface.blit(surf, r)


POWERUP_LABELS = {
    "triple": "TRIPLE SHOT!",
    "quint": "QUINTUPLE!",
    "missile_overcharge": "OVERCHARGE!",
    "shield": "SHIELD UP!",
    "life": "+1 LIFE",
}

# --------------------------------------------------------------------------
# PLAYER SHIP
# --------------------------------------------------------------------------
def player_ship_points(cx, cy, scale=1.0):
    """The player's swept-wing interceptor silhouette, reusable at any size/position -
    used both for the actual ship and for the heart HUD icons so they visually match."""
    return [
        (cx, cy - 24 * scale),
        (cx + 5 * scale, cy - 4 * scale),
        (cx + 18 * scale, cy + 10 * scale),
        (cx + 8 * scale, cy + 15 * scale),
        (cx + 3 * scale, cy + 9 * scale),
        (cx, cy + 14 * scale),
        (cx - 3 * scale, cy + 9 * scale),
        (cx - 8 * scale, cy + 15 * scale),
        (cx - 18 * scale, cy + 10 * scale),
        (cx - 5 * scale, cy - 4 * scale),
    ]


PLAYER_RADIUS = 16
PLAYER_BASE_SPEED = 480.0  # px/sec
MISSILE_COOLDOWN = 5.0

# --- hearts / regenerating health bar ---
STARTING_HEARTS = 4  # 3 hearts shown at start (hearts displayed = hearts - 1)
MAX_HEARTS = 6
MAX_HEALTH = 100.0
REGEN_INTERVAL = 3.0  # a small regen tick happens this often ...
REGEN_AMOUNT = 5.0  # ... and heals this many of the 100 health points each tick (slow full-refill: ~60s)
MAX_HIT_FRACTION = 0.6  # a single hit can never remove more than this fraction of a full heart's bar
BASE_HP_PER_DAMAGE_UNIT = 16.0  # 1 "damage unit" (a basic enemy bullet) at stage 1
DIFFICULTY_DAMAGE_SCALE = 0.06  # +6% damage per stage/endless-loop of difficulty
HEART_LOSS_INVULN = 3.0  # invincibility window after losing a whole heart
HIT_INVULN = 0.35  # brief graze-immunity after any damaging hit, so bullets can't double-tick in one frame


class Player:
    def __init__(self):
        self.x = WIDTH / 2
        self.y = HEIGHT - 140
        self.radius = PLAYER_RADIUS
        self.hearts = STARTING_HEARTS
        self.health = MAX_HEALTH
        self.regen_cd = REGEN_INTERVAL
        self.score = 0
        self.invuln_timer = 1.5  # brief spawn invulnerability
        self.hit_flash = 0.0

        self.fire_cooldown = 0.0
        self.fire_rate = 0.11

        self.missile_cd = 0.0  # counts DOWN to 0 = ready
        self.missile_overcharge_timer = 0.0

        self.spread_timer = 0.0
        self.spread_level = 0  # 0 normal, 1 triple, 2 quintuple
        self.base_spread_level = 0  # permanent floor set by set_tier() - triple/quint become baseline on later stages

        self.shield_timer = 0.0

        self.thruster_phase = 0.0

    @property
    def speed(self):
        return PLAYER_BASE_SPEED

    @property
    def invulnerable(self):
        return self.invuln_timer > 0 or self.shield_timer > 0

    @property
    def lives(self):
        # Kept as a read-only alias so anything checking "is the player still alive"
        # (player.lives <= 0) keeps working now that lives are heart-based.
        return self.hearts

    def set_tier(self, tier):
        """Called whenever a stage loads. From stage 5 onward triple spread is
        the permanent baseline; from stage 8 onward quintuple is. This never
        removes a HIGHER level the player currently has active from a powerup -
        it only raises the floor."""
        self.base_spread_level = 2 if tier >= 8 else (1 if tier >= 5 else 0)
        if self.spread_level < self.base_spread_level:
            self.spread_level = self.base_spread_level

    def apply_powerup(self, kind):
        if kind == "triple":
            self.spread_level = max(self.spread_level, 1)
            self.spread_timer = 10.0
        elif kind == "quint":
            self.spread_level = 2
            self.spread_timer = 9.0
        elif kind == "missile_overcharge":
            self.missile_overcharge_timer = 6.0
        elif kind == "shield":
            self.shield_timer = 6.0
        elif kind == "life":
            if self.hearts < MAX_HEARTS:
                self.hearts += 1
            else:
                self.score += 300  # already at the heart cap - convert to a score bonus instead

    def take_hit(self, damage_units=1.0, tier=1):
        """damage_units: relative danger of the hit (~1.0 = a basic enemy bullet).
        tier: current stage/difficulty, used to scale damage up on later stages.
        A hit is always clamped so it can never remove more than MAX_HIT_FRACTION of
        one heart's bar in a single shot - the ship can never be one-shot."""
        if self.invulnerable:
            return False
        scale = 1.0 + (max(1, tier) - 1) * DIFFICULTY_DAMAGE_SCALE
        dmg = min(BASE_HP_PER_DAMAGE_UNIT * damage_units * scale, MAX_HEALTH * MAX_HIT_FRACTION)
        self.health -= dmg
        self.hit_flash = 0.2
        play_sound("player_hit")
        if self.health <= 0:
            self.hearts -= 1
            if self.hearts > 0:
                self.health = MAX_HEALTH
                self.invuln_timer = HEART_LOSS_INVULN
                play_sound("life_lost")
            else:
                self.health = 0.0
                self.hearts = 0
        else:
            self.invuln_timer = max(self.invuln_timer, HIT_INVULN)
        return True

    def update(self, dt, keys, mouse_pos, mouse_moved, firing_primary, missile_tap):
        # --- movement ---
        moved = False
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:
            self.x -= self.speed * dt
            moved = True
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
            self.x += self.speed * dt
            moved = True
        if keys[pygame.K_UP] or keys[pygame.K_w]:
            self.y -= self.speed * dt
            moved = True
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:
            self.y += self.speed * dt
            moved = True
        if mouse_moved and not moved:
            self.x += (mouse_pos[0] - self.x) * min(1.0, dt * 14)
            self.y += (mouse_pos[1] - self.y) * min(1.0, dt * 14)

        margin = self.radius + 6
        self.x = max(margin, min(WIDTH - margin, self.x))
        self.y = max(margin, min(HEIGHT - margin, self.y))

        # --- timers ---
        if self.invuln_timer > 0:
            self.invuln_timer -= dt
        if self.hit_flash > 0:
            self.hit_flash -= dt
        if self.missile_cd > 0:
            self.missile_cd -= dt
        if self.missile_overcharge_timer > 0:
            self.missile_overcharge_timer -= dt
        if self.shield_timer > 0:
            self.shield_timer -= dt
        if self.spread_timer > 0:
            self.spread_timer -= dt
            if self.spread_timer <= 0:
                self.spread_level = self.base_spread_level
        if self.fire_cooldown > 0:
            self.fire_cooldown -= dt

        if self.hearts > 0:
            self.regen_cd -= dt
            if self.regen_cd <= 0:
                self.regen_cd = REGEN_INTERVAL
                if self.health < MAX_HEALTH:
                    self.health = min(MAX_HEALTH, self.health + REGEN_AMOUNT)

        self.thruster_phase += dt * 10

        new_bullets = []
        new_missiles = []

        if firing_primary and self.fire_cooldown <= 0:
            self.fire_cooldown = self.fire_rate
            new_bullets.extend(self._make_bullets())
            play_sound("laser")

        missile_ready = self.missile_cd <= 0
        overcharged = self.missile_overcharge_timer > 0
        if missile_tap and (missile_ready or overcharged):
            new_missiles.append(PlayerMissile(self.x, self.y - 10))
            if not overcharged:
                self.missile_cd = MISSILE_COOLDOWN
            else:
                self.missile_cd = 0.0
            play_sound("missile")

        return new_bullets, new_missiles

    def _make_bullets(self):
        bullets = []
        if self.spread_level == 0:
            bullets.append(PlayerBullet(self.x, self.y - 18, 0))
        elif self.spread_level == 1:
            for ang in (-0.16, 0.0, 0.16):
                bullets.append(PlayerBullet(self.x, self.y - 18, ang))
        else:
            for ang in (-0.34, -0.17, 0.0, 0.17, 0.34):
                bullets.append(PlayerBullet(self.x, self.y - 18, ang))
        return bullets

    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), self.radius * 2, self.radius * 2)

    def draw(self, surface):
        # engine thruster flicker
        flick = 6 + math.sin(self.thruster_phase) * 3
        draw_glow_circle(surface, (int(self.x - 9), int(self.y + 19)), max(2, flick * 0.5), ORANGE, layers=3, base_alpha=90)
        draw_glow_circle(surface, (int(self.x + 9), int(self.y + 19)), max(2, flick * 0.5), ORANGE, layers=3, base_alpha=90)

        body_color = flash_color(CYAN, self.hit_flash)
        # sleek swept-wing interceptor silhouette - distinct from the enemy roster
        pts = player_ship_points(self.x, self.y)
        if self.invuln_timer > 0 and int(self.invuln_timer * 12) % 2 == 0:
            pass  # brief spawn-blink
        else:
            draw_glow_poly(surface, pts, body_color, layers=3, base_alpha=70)
            draw_aa_circle(surface, ORANGE, (int(self.x + 18), int(self.y + 10)), 2)
            draw_aa_circle(surface, ORANGE, (int(self.x - 18), int(self.y + 10)), 2)
        draw_aa_circle(surface, WHITE, (int(self.x), int(self.y - 8)), 3)

        if self.shield_timer > 0:
            alpha = 90 + int(40 * math.sin(self.thruster_phase * 2))
            s = pygame.Surface((80, 80), pygame.SRCALPHA)
            draw_aa_circle(s, (*BLUE, alpha), (40, 40), 30)
            surface.blit(s, (self.x - 40, self.y - 40), special_flags=pygame.BLEND_RGBA_ADD)

        bar_w, bar_h, gap = 40, 5, 2

        # regenerating health bar (current heart) - sits right above the missile cooldown bar
        health_y = self.y + 19
        hx = self.x - bar_w / 2
        back = pygame.Surface((bar_w, bar_h), pygame.SRCALPHA)
        pygame.draw.rect(back, (255, 255, 255, 55), (0, 0, bar_w, bar_h), border_radius=2)
        surface.blit(back, (hx, health_y))
        hfrac = max(0.0, min(1.0, self.health / MAX_HEALTH))
        hcolor = GREEN if hfrac > 0.5 else (ORANGE if hfrac > 0.25 else RED)
        hfg = pygame.Surface((max(1, int(bar_w * hfrac)), bar_h), pygame.SRCALPHA)
        pygame.draw.rect(hfg, (*hcolor, 230), (0, 0, max(1, int(bar_w * hfrac)), bar_h), border_radius=2)
        surface.blit(hfg, (hx, health_y))

        # missile cooldown indicator: translucent bar directly under the health bar
        missile_y = health_y + bar_h + gap
        back = pygame.Surface((bar_w, bar_h), pygame.SRCALPHA)
        pygame.draw.rect(back, (255, 255, 255, 55), (0, 0, bar_w, bar_h), border_radius=2)
        surface.blit(back, (hx, missile_y))
        if self.missile_overcharge_timer > 0:
            frac = 1.0
            col = (*ORANGE, 220)
        else:
            frac = 1.0 - max(0.0, min(1.0, self.missile_cd / MISSILE_COOLDOWN))
            col = (*ORANGE, 200) if frac >= 1.0 else (*GRAY, 160)
        fg = pygame.Surface((max(1, int(bar_w * frac)), bar_h), pygame.SRCALPHA)
        pygame.draw.rect(fg, col, (0, 0, max(1, int(bar_w * frac)), bar_h), border_radius=2)
        surface.blit(fg, (hx, missile_y))


# --------------------------------------------------------------------------
# PROJECTILES
# --------------------------------------------------------------------------
class PlayerBullet:
    SPEED = 820.0

    def __init__(self, x, y, angle_offset=0.0):
        self.x, self.y = x, y
        base = -math.pi / 2
        self.dx = math.cos(base + angle_offset)
        self.dy = math.sin(base + angle_offset)
        self.radius = 4
        self.dead = False
        self.damage = 1

    def update(self, dt):
        self.x += self.dx * self.SPEED * dt
        self.y += self.dy * self.SPEED * dt
        if self.y < -20 or self.x < -20 or self.x > WIDTH + 20:
            self.dead = True

    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), self.radius * 2, self.radius * 2)

    def draw(self, surface):
        draw_glow_circle(surface, (int(self.x), int(self.y)), self.radius, CYAN, layers=3, base_alpha=120)


class PlayerMissile:
    SPEED = 560.0
    TURN_RATE = 4.4
    RETARGET_INTERVAL = 0.35

    def __init__(self, x, y):
        self.x, self.y = x, y
        self.dx, self.dy = 0.0, -1.0
        self.radius = 6
        self.dead = False
        self.damage = 4
        self.target = None
        self.trail = []
        self._retarget_cd = 0.0

    def _pick_target(self, enemies):
        """Prefer targets the missile is already roughly facing over the raw-nearest
        one - locking onto something behind or far to the side just makes it loop
        uselessly given a limited turn rate."""
        best, best_score = None, 1e18
        for e in enemies:
            if getattr(e, "dead", False):
                continue
            tx, ty = e.x - self.x, e.y - self.y
            dist = math.hypot(tx, ty) or 1
            cos_ang = (self.dx * tx + self.dy * ty) / dist  # 1 = dead ahead, -1 = behind
            angle_penalty = (1.0 - cos_ang) * 260
            score = dist + angle_penalty
            if score < best_score:
                best_score = score
                best = e
        return best

    def update(self, dt, enemies=None):
        enemies = enemies or []
        self._retarget_cd -= dt
        if getattr(self.target, "dead", True) or self._retarget_cd <= 0:
            self._retarget_cd = self.RETARGET_INTERVAL
            new_target = self._pick_target(enemies)
            if new_target is not None:
                self.target = new_target

        if self.target is not None and not getattr(self.target, "dead", True):
            tx, ty = self.target.x - self.x, self.target.y - self.y
            dist = math.hypot(tx, ty) or 1
            tx, ty = tx / dist, ty / dist
            self.dx += (tx - self.dx) * min(1.0, self.TURN_RATE * dt)
            self.dy += (ty - self.dy) * min(1.0, self.TURN_RATE * dt)
            norm = math.hypot(self.dx, self.dy) or 1
            self.dx, self.dy = self.dx / norm, self.dy / norm

        self.trail.append((self.x, self.y))
        if len(self.trail) > 6:
            self.trail.pop(0)

        self.x += self.dx * self.SPEED * dt
        self.y += self.dy * self.SPEED * dt
        if self.y < -30 or self.y > HEIGHT + 30 or self.x < -30 or self.x > WIDTH + 30:
            self.dead = True

    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), self.radius * 2, self.radius * 2)

    def draw(self, surface):
        for i, (tx, ty) in enumerate(self.trail):
            a = int(120 * (i / max(1, len(self.trail))))
            s = pygame.Surface((10, 10), pygame.SRCALPHA)
            draw_aa_circle(s, (*ORANGE, a), (5, 5), 3)
            surface.blit(s, (tx - 5, ty - 5))
        draw_glow_circle(surface, (int(self.x), int(self.y)), self.radius, ORANGE, layers=4, base_alpha=140)


class EnemyBullet:
    def __init__(self, x, y, dx, dy, speed=260.0, color=RED, radius=5, damage=1):
        self.x, self.y = x, y
        norm = math.hypot(dx, dy) or 1
        self.dx, self.dy = dx / norm, dy / norm
        self.speed = speed
        self.color = color
        self.radius = radius
        self.dead = False
        self.damage = damage

    def update(self, dt):
        self.x += self.dx * self.speed * dt
        self.y += self.dy * self.speed * dt
        if self.y < -30 or self.y > HEIGHT + 30 or self.x < -30 or self.x > WIDTH + 30:
            self.dead = True

    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), self.radius * 2, self.radius * 2)

    def draw(self, surface):
        draw_glow_circle(surface, (int(self.x), int(self.y)), self.radius, self.color, layers=3, base_alpha=110)


POWERUP_SIZE = 30


class PowerUp:
    LIFESPAN = 9.0

    def __init__(self, x, y, kind):
        self.x, self.y = x, y
        self.kind = kind
        self.letter, self.color, _ = POWERUP_INFO[kind]
        self.vy = 90.0
        self.lifespan = PowerUp.LIFESPAN
        self.dead = False

    def update(self, dt):
        self.y += self.vy * dt
        self.lifespan -= dt
        if self.lifespan <= 0 or self.y > HEIGHT + 30:
            self.dead = True

    def rect(self):
        half = POWERUP_SIZE // 2
        return pygame.Rect(int(self.x - half), int(self.y - half), POWERUP_SIZE, POWERUP_SIZE)

    def draw(self, surface):
        if self.lifespan < 1.5 and int(self.lifespan * 8) % 2 == 0:
            return
        r = self.rect()
        draw_glow_rect(surface, r, self.color, layers=4, expand=2, base_alpha=70, radius=14)
        draw_text_center(surface, self.letter, FONT_SMALL, (10, 10, 15), r.center)

# --------------------------------------------------------------------------
# ENEMIES (regular, non-boss)
# --------------------------------------------------------------------------
class Enemy:
    """Base class for regular (non-boss) enemies. `kind` picks movement/attack
    behavior; stats scale up with the `tier` passed in (roughly = stage number)."""

    KIND_STATS = {
        # kind: (hp, radius, color, score)
        "drone": (2, 14, TEAL, 80),
        "zigzag": (3, 14, PURPLE, 110),
        "turret": (5, 17, ORANGE, 160),
        "kamikaze": (2, 13, RED, 90),
        "shielded": (7, 18, BLUE, 200),
    }

    def __init__(self, x, y, kind, tier=1):
        self.x, self.y = x, y
        self.kind = kind
        hp, radius, color, score = self.KIND_STATS[kind]
        tier_mult = 1.0 + (tier - 1) * 0.18
        self.hp = max(1, round(hp * tier_mult))
        self.max_hp = self.hp
        self.radius = radius
        self.color = color
        self.score_value = int(score * (1.0 + (tier - 1) * 0.12))
        self.tier = tier
        self.dead = False
        self.hit_flash = 0.0
        self.t = random.uniform(0, math.tau)
        self.vy = random.uniform(70, 100) * (1.0 + (tier - 1) * 0.05)
        self.base_x = x
        self.shoot_cd = random.uniform(2.0, 3.5) if kind == "drone" else random.uniform(0.8, 2.2)
        self.contact_damage = 1
        self.shield_hp = 3 if kind == "shielded" else 0

    def hit(self, dmg=1):
        if self.shield_hp > 0:
            self.shield_hp -= dmg
            self.hit_flash = 0.15
            play_sound("hit")
            return False
        self.hp -= dmg
        self.hit_flash = 0.15
        if self.hp <= 0:
            self.dead = True
            return True
        play_sound("hit")
        return False

    def update(self, dt, player, bullets_out):
        self.t += dt
        if self.hit_flash > 0:
            self.hit_flash -= dt

        if self.kind == "drone":
            self.y += self.vy * dt
        elif self.kind == "zigzag":
            self.y += self.vy * dt * 0.8
            self.x = self.base_x + math.sin(self.t * 3.0) * 70
        elif self.kind == "turret":
            if self.y < 170:
                self.y += self.vy * dt
            else:
                self.x = self.base_x + math.sin(self.t * 1.2) * 40
        elif self.kind == "kamikaze":
            dx, dy = player.x - self.x, player.y - self.y
            dist = math.hypot(dx, dy) or 1
            if self.y < 90:
                self.y += self.vy * dt
            else:
                self.x += dx / dist * self.vy * 1.6 * dt
                self.y += dy / dist * self.vy * 1.6 * dt
        elif self.kind == "shielded":
            if self.y < 200:
                self.y += self.vy * dt
            else:
                self.x = self.base_x + math.sin(self.t * 0.8) * 55

        self.shoot_cd -= dt
        if self.shoot_cd <= 0 and self.y > 0 and self.y < HEIGHT - 60 and self.kind in ("turret", "zigzag", "shielded", "drone"):
            if self.kind == "drone":
                self.shoot_cd = random.uniform(2.6, 4.0) / (1.0 + (self.tier - 1) * 0.08)
                bullet_speed = 190 + self.tier * 5
            else:
                self.shoot_cd = random.uniform(1.4, 2.6) / (1.0 + (self.tier - 1) * 0.08)
                bullet_speed = 230 + self.tier * 6
            dx, dy = player.x - self.x, player.y - self.y
            bullets_out.append(EnemyBullet(self.x, self.y, dx, dy, speed=bullet_speed, color=self.color))

        if self.y > HEIGHT + 60 or self.x < -80 or self.x > WIDTH + 80:
            self.dead = True

    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), self.radius * 2, self.radius * 2)

    def draw(self, surface):
        col = flash_color(self.color, self.hit_flash)
        x, y, r = self.x, self.y, self.radius

        if self.kind == "drone":
            # small rounded scout - compact diamond with a sensor eye
            pts = [(x, y - r), (x + r * 0.75, y), (x, y + r * 0.6), (x - r * 0.75, y)]
            draw_glow_poly(surface, pts, col, layers=2, base_alpha=55)
            draw_aa_circle(surface, WHITE, (int(x), int(y - r * 0.15)), 2)
        elif self.kind == "zigzag":
            # swept boomerang/chevron shape, echoing its side-to-side flight
            pts = [
                (x, y - r * 0.2), (x + r * 1.1, y - r), (x + r * 0.3, y + r * 0.25),
                (x, y + r * 1.05), (x - r * 0.3, y + r * 0.25), (x - r * 1.1, y - r),
            ]
            draw_glow_poly(surface, pts, col, layers=2, base_alpha=55)
        elif self.kind == "turret":
            # hexagonal hull with a barrel aimed down at the player
            pts = [(x + r * math.cos(math.radians(a)), y + r * math.sin(math.radians(a))) for a in (90, 150, 210, 270, 330, 30)]
            draw_glow_poly(surface, pts, col, layers=2, base_alpha=55)
            pygame.draw.rect(surface, col, (x - 3, y, 6, int(r * 0.9)), border_radius=2)
            draw_aa_circle(surface, WHITE, (int(x), int(y)), 2)
        elif self.kind == "kamikaze":
            # jagged spike/star silhouette - reads as aggressive on sight
            pts = []
            for i in range(8):
                ang = math.pi / 4 * i - math.pi / 2
                rr = r * (1.15 if i % 2 == 0 else 0.42)
                pts.append((x + rr * math.cos(ang), y + rr * math.sin(ang)))
            draw_glow_poly(surface, pts, col, layers=3, expand=2, base_alpha=75)
        elif self.kind == "shielded":
            # rounded octagonal hull with visible armor plating
            pts = [(x + r * math.cos(math.tau * i / 8), y + r * math.sin(math.tau * i / 8)) for i in range(8)]
            draw_glow_poly(surface, pts, col, layers=2, base_alpha=55)
            for i in range(4):
                ang = math.tau * i / 4 + math.pi / 8
                px, py = x + r * 0.55 * math.cos(ang), y + r * 0.55 * math.sin(ang)
                pygame.draw.line(surface, (20, 24, 34), (x, y), (px, py), 2)
        else:
            pts = [(x, y + r), (x + r, y - r * 0.4), (x, y - r), (x - r, y - r * 0.4)]
            draw_glow_poly(surface, pts, col, layers=2, base_alpha=55)

        if self.shield_hp > 0:
            s = pygame.Surface((self.radius * 3, self.radius * 3), pygame.SRCALPHA)
            draw_aa_circle(s, (*BLUE, 90), (self.radius * 1.5, self.radius * 1.5), self.radius * 1.35)
            surface.blit(s, (self.x - self.radius * 1.5, self.y - self.radius * 1.5))
        if self.max_hp > 1:
            w = self.radius * 2
            frac = max(0.0, self.hp / self.max_hp)
            back = pygame.Surface((w, 4), pygame.SRCALPHA)
            pygame.draw.rect(back, (0, 0, 0, 140), (0, 0, w, 4))
            surface.blit(back, (self.x - w / 2, self.y - self.radius - 10))
            fg = pygame.Surface((max(1, int(w * frac)), 4), pygame.SRCALPHA)
            pygame.draw.rect(fg, (*GREEN, 220), (0, 0, max(1, int(w * frac)), 4))
            surface.blit(fg, (self.x - w / 2, self.y - self.radius - 10))

# --------------------------------------------------------------------------
# BOSSES - shared infrastructure (hp bar, hit-flash, drift-in, death sequence)
# but each of the 10 subclasses below implements its OWN unique attack
# pattern set in `attack_update`, chosen by `name`/hp-phase.
# --------------------------------------------------------------------------
class Boss:
    NAME = "Unknown Hostile"
    COLOR = RED

    def __init__(self, stage):
        self.stage = stage
        self.x = WIDTH / 2
        self.y = -140
        self.target_y = 190
        self.radius = 46
        base_hp = 90 + stage * 34
        self.hp = base_hp
        self.max_hp = base_hp
        self.dead = False
        self.hit_flash = 0.0
        self.t = 0.0
        self.entering = True
        self.phase = 0  # 0 = >66% hp, 1 = 34-66%, 2 = <34%
        self.minions_spawned = 0
        self.score_value = 4000 + stage * 800

    @property
    def hp_frac(self):
        return max(0.0, self.hp / self.max_hp)

    def hit(self, dmg=1):
        if self.entering:
            return False
        self.hp -= dmg
        self.hit_flash = 0.15
        new_phase = 0 if self.hp_frac > 0.66 else (1 if self.hp_frac > 0.33 else 2)
        if new_phase != self.phase:
            self.phase = new_phase
        if self.hp <= 0:
            self.hp = 0
            self.dead = True
            return True
        return False

    def update(self, dt, player, bullets_out, particles, enemies_out):
        self.t += dt
        if self.hit_flash > 0:
            self.hit_flash -= dt
        if self.entering:
            self.y += (self.target_y - self.y) * min(1.0, dt * 1.6)
            if abs(self.y - self.target_y) < 2:
                self.entering = False
            return
        self.attack_update(dt, player, bullets_out, particles, enemies_out)

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        """Overridden per-boss. Default: slow drift + aimed volley."""
        self.x = WIDTH / 2 + math.sin(self.t * 0.6) * 140
        if int(self.t * 2) % 3 == 0 and int(self.t * 30) % 30 == 0:
            self._aimed_shot(player, bullets_out)

    def _aimed_shot(self, player, bullets_out, speed=250, color=None):
        dx, dy = player.x - self.x, player.y - self.y
        bullets_out.append(EnemyBullet(self.x, self.y, dx, dy, speed=speed, color=color or self.COLOR, radius=6))

    def _ring_shot(self, bullets_out, count=12, speed=190, color=None):
        for i in range(count):
            ang = math.tau * i / count + self.t
            bullets_out.append(EnemyBullet(self.x, self.y, math.cos(ang), math.sin(ang), speed=speed, color=color or self.COLOR, radius=6))

    def rect(self):
        return pygame.Rect(int(self.x - self.radius), int(self.y - self.radius), self.radius * 2, self.radius * 2)

    def draw_body(self, surface, points):
        col = flash_color(self.COLOR, self.hit_flash)
        draw_glow_poly(surface, points, col, layers=4, expand=3, base_alpha=65)

    def draw_hp_bar(self, surface):
        w = WIDTH * 0.72
        h = 14
        x = (WIDTH - w) / 2
        y = 76
        pygame.draw.rect(surface, (0, 0, 0, 160), (x - 3, y - 3, w + 6, h + 6), border_radius=6)
        pygame.draw.rect(surface, (30, 32, 45), (x, y, w, h), border_radius=5)
        fw = max(0, int(w * self.hp_frac))
        col = GREEN if self.hp_frac > 0.5 else (ORANGE if self.hp_frac > 0.25 else RED)
        if fw > 0:
            pygame.draw.rect(surface, col, (x, y, fw, h), border_radius=5)
        draw_text_center(surface, self.NAME, FONT_TINY, WHITE, (WIDTH / 2, y - 14))

    def draw_hull(self, surface):
        """Overridden per-boss for a unique silhouette. Default: plain pentagon hull."""
        pts = [
            (self.x, self.y + self.radius),
            (self.x + self.radius, self.y),
            (self.x + self.radius * 0.5, self.y - self.radius),
            (self.x - self.radius * 0.5, self.y - self.radius),
            (self.x - self.radius, self.y),
        ]
        self.draw_body(surface, pts)

    def draw(self, surface):
        self.draw_hull(surface)
        self.draw_hp_bar(surface)


# ---- 1. Recon Drone: simple telegraphed fan volleys, side-to-side drift ----
class Boss1_ReconDrone(Boss):
    NAME = "RECON DRONE"
    COLOR = TEAL

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.9) * 170
        self._cd = getattr(self, "_cd", 1.8) - dt
        if self._cd <= 0:
            self._cd = 2.1 if self.phase == 0 else (1.6 if self.phase == 1 else 1.15)
            spread = 3 if self.phase == 0 else (4 if self.phase == 1 else 5)
            base = math.atan2(player.y - self.y, player.x - self.x)
            for i in range(spread):
                ang = base + (i - spread // 2) * 0.16
                bullets_out.append(EnemyBullet(self.x, self.y, math.cos(ang), math.sin(ang), speed=205, color=self.COLOR))
            play_sound("hit")

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        pts = [(x, y - r * 1.15), (x + r * 0.55, y - r * 0.1), (x, y + r * 0.75), (x - r * 0.55, y - r * 0.1)]
        self.draw_body(surface, pts)
        draw_glow_circle(surface, (int(x), int(y - r * 0.1)), 11, WHITE, layers=2, base_alpha=100)
        draw_aa_circle(surface, self.COLOR, (int(x), int(y - r * 0.1)), 6)
        for wx in (-r * 0.85, r * 0.85):
            draw_aa_circle(surface, self.COLOR, (int(x + wx), int(y - r * 0.1)), 4)


# ---- 2. Twin Turret Cruiser: two firing points alternate spreads ----
class Boss2_TwinTurret(Boss):
    NAME = "TWIN TURRET CRUISER"
    COLOR = ORANGE

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.5) * 120
        self._cd = getattr(self, "_cd", 0.9) - dt
        self._which = getattr(self, "_which", 0)
        if self._cd <= 0:
            self._cd = 0.75 if self.phase < 2 else 0.5
            ox = -60 if self._which == 0 else 60
            self._which = 1 - self._which
            fx, fy = self.x + ox, self.y + 16
            dx, dy = player.x - fx, player.y - fy
            for off in (-0.12, 0, 0.12):
                ang = math.atan2(dy, dx) + off
                bullets_out.append(EnemyBullet(fx, fy, math.cos(ang), math.sin(ang), speed=270, color=self.COLOR))
            play_sound("hit")

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        pts = [
            (x - r * 1.3, y - r * 0.25), (x - r * 0.55, y - r * 0.85), (x + r * 0.55, y - r * 0.85),
            (x + r * 1.3, y - r * 0.25), (x + r * 0.85, y + r * 0.55), (x - r * 0.85, y + r * 0.55),
        ]
        self.draw_body(surface, pts)
        for ox in (-60, 60):
            draw_glow_circle(surface, (int(x + ox), int(y + 16)), 9, YELLOW, layers=2, base_alpha=90)


# ---- 3. Plasma Interceptor: fast horizontal dash w/ damaging trail ----
class Boss3_PlasmaInterceptor(Boss):
    NAME = "PLASMA INTERCEPTOR"
    COLOR = MAGENTA

    def __init__(self, stage):
        super().__init__(stage)
        self.dash_timer = 2.0
        self.dashing = False
        self.dash_dir = 1

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        speed_mult = 1.0 if self.phase == 0 else (1.3 if self.phase == 1 else 1.6)
        if self.dashing:
            self.x += self.dash_dir * 620 * speed_mult * dt
            if random.random() < 0.5:
                particles.append(Particle(self.x, self.y, 0, 0, 0.3, self.COLOR, size=6))
            if self.x < 60 or self.x > WIDTH - 60:
                self.dashing = False
                self.dash_timer = 1.1 / speed_mult
                self._ring_shot(bullets_out, count=10, speed=200)
        else:
            self.y = self.target_y + math.sin(self.t * 1.5) * 20
            self.dash_timer -= dt
            if self.dash_timer <= 0:
                self.dashing = True
                self.dash_dir = 1 if player.x > self.x else -1
                if self.phase == 2:
                    self._aimed_shot(player, bullets_out, speed=300)

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        facing = self.dash_dir if getattr(self, "dashing", False) else 0
        pts = [
            (x + facing * r * 1.3, y),
            (x + r * 0.25 - facing * r * 0.2, y - r * 0.35),
            (x - facing * r * 0.9, y - r * 0.7),
            (x - facing * r * 1.1, y),
            (x - facing * r * 0.9, y + r * 0.7),
            (x + r * 0.25 - facing * r * 0.2, y + r * 0.35),
        ] if facing else [
            (x, y - r * 1.3), (x + r * 0.35, y + r * 0.1), (x + r * 1.0, y + r * 0.9),
            (x, y + r * 0.4), (x - r * 1.0, y + r * 0.9), (x - r * 0.35, y + r * 0.1),
        ]
        self.draw_body(surface, pts)


# ---- 4. Swarm Carrier: periodically deploys drone minions, slow homing orbs ----
class Boss4_SwarmCarrier(Boss):
    NAME = "SWARM CARRIER"
    COLOR = GREEN

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        pts = [(x + r * 1.1 * math.cos(math.radians(a)), y + r * 0.85 * math.sin(math.radians(a))) for a in (0, 60, 120, 180, 240, 300)]
        self.draw_body(surface, pts)
        pygame.draw.line(surface, (20, 30, 24), (x - r * 0.7, y), (x + r * 0.7, y), 3)
        for ox in (-r * 0.4, r * 0.4):
            pygame.draw.rect(surface, (20, 30, 24), (x + ox - 5, y - r * 0.5, 10, r), border_radius=2)

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.4) * 100
        self._spawn_cd = getattr(self, "_spawn_cd", 3.0) - dt
        self._orb_cd = getattr(self, "_orb_cd", 1.6) - dt
        if self._spawn_cd <= 0 and self.minions_spawned < 40:
            self._spawn_cd = 4.5 if self.phase == 0 else (3.2 if self.phase == 1 else 2.2)
            for ox in (-70, 70):
                enemies_out.append(Enemy(self.x + ox, self.y + 30, "drone", tier=self.stage))
            self.minions_spawned += 2
        if self._orb_cd <= 0:
            self._orb_cd = 1.8 if self.phase < 2 else 1.1
            dx, dy = player.x - self.x, player.y - self.y
            bullets_out.append(EnemyBullet(self.x, self.y, dx, dy, speed=140, color=self.COLOR, radius=8))


# ---- 5. Laser Lattice Frigate: telegraphed vertical laser beams + bullet rings ----
class Boss5_LaserFrigate(Boss):
    NAME = "LASER LATTICE FRIGATE"
    COLOR = PURPLE

    def __init__(self, stage):
        super().__init__(stage)
        self.lasers = []

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.35) * 90
        self._laser_cd = getattr(self, "_laser_cd", 2.4) - dt
        self._ring_cd = getattr(self, "_ring_cd", 1.9) - dt
        if self._laser_cd <= 0:
            self._laser_cd = 3.0 if self.phase == 0 else (2.2 if self.phase == 1 else 1.6)
            count = 2 if self.phase < 2 else 3
            xs = random.sample(range(60, WIDTH - 60, 40), min(count, 3))
            for lx in xs[:count]:
                self.lasers.append(BossLaser(lx))
        if self._ring_cd <= 0:
            self._ring_cd = 2.0
            self._ring_shot(bullets_out, count=14, speed=170)
        for laser in self.lasers:
            laser.update(dt)
            if laser.state == "firing" and not laser.hit_applied and laser.rect().collidepoint(player.x, player.y):
                laser.hit_applied = True
                player.take_hit(1.6, tier=self.stage)
        self.lasers = [l for l in self.lasers if l.state != "done"]

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        pts = [
            (x - r * 1.5, y - r * 0.22), (x - r * 0.35, y - r * 0.65), (x + r * 0.35, y - r * 0.65),
            (x + r * 1.5, y - r * 0.22), (x + r * 1.5, y + r * 0.22), (x + r * 0.35, y + r * 0.65),
            (x - r * 0.35, y + r * 0.65), (x - r * 1.5, y + r * 0.22),
        ]
        self.draw_body(surface, pts)
        col = flash_color(self.COLOR, self.hit_flash)
        for lx in (-r * 0.9, -r * 0.3, r * 0.3, r * 0.9):
            pygame.draw.line(surface, col, (x + lx, y - r * 0.4), (x + lx, y + r * 0.4), 2)

    def draw(self, surface):
        for laser in self.lasers:
            laser.draw(surface)
        super().draw(surface)


class BossLaser:
    WARNING_DURATION = 1.4
    FIRE_DURATION = 0.4

    def __init__(self, x, width=46):
        self.x = x
        self.width = width
        self.timer = 0.0
        self.state = "warning"
        self.hit_applied = False

    def update(self, dt):
        self.timer += dt
        if self.state == "warning" and self.timer >= self.WARNING_DURATION:
            self.state = "firing"
            self.timer = 0.0
            play_sound("laser")
        elif self.state == "firing" and self.timer >= self.FIRE_DURATION:
            self.state = "done"

    def rect(self):
        return pygame.Rect(int(self.x - self.width / 2), 0, int(self.width), HEIGHT)

    def draw(self, surface):
        half = self.width / 2
        if self.state == "warning":
            alpha = int(90 + 90 * abs(math.sin(self.timer * 10)))
            s = pygame.Surface((int(self.width), HEIGHT), pygame.SRCALPHA)
            s.fill((*RED, alpha // 3))
            surface.blit(s, (self.x - half, 0))
        elif self.state == "firing":
            s = pygame.Surface((int(self.width), HEIGHT), pygame.SRCALPHA)
            s.fill((*WHITE, 230))
            surface.blit(s, (self.x - half, 0))
            s2 = pygame.Surface((int(self.width * 1.6), HEIGHT), pygame.SRCALPHA)
            s2.fill((*RED, 90))
            surface.blit(s2, (self.x - half * 1.6, 0))


# ---- 6. Void Serpent: sinusoidal glide, spiral bullet patterns ----
class Boss6_VoidSerpent(Boss):
    NAME = "VOID SERPENT"
    COLOR = BLUE

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.8) * 190
        self.y = self.target_y + math.sin(self.t * 1.6) * 45
        self._cd = getattr(self, "_cd", 0.1) - dt
        if self._cd <= 0:
            self._cd = 0.06 if self.phase < 2 else 0.04
            self._spiral_ang = getattr(self, "_spiral_ang", 0.0) + 0.45
            for k in range(2 if self.phase < 1 else 3):
                ang = self._spiral_ang + k * math.tau / 3
                bullets_out.append(EnemyBullet(self.x, self.y, math.cos(ang), math.sin(ang), speed=190, color=self.COLOR, radius=5))

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        col = flash_color(self.COLOR, self.hit_flash)
        # trailing serpentine segments, shrinking away from the head
        for i in range(4, 0, -1):
            seg_x = x - math.sin(self.t * 0.8 - i * 0.5) * 40 * i * 0.4
            seg_y = y - i * r * 0.55 + math.sin(self.t * 1.6 - i * 0.4) * 8
            seg_r = r * (0.85 - i * 0.12)
            draw_glow_circle(surface, (int(seg_x), int(seg_y)), max(6, seg_r), col, layers=2, base_alpha=45)
        pts = [(x, y - r * 0.9), (x + r * 0.85, y + r * 0.15), (x, y + r * 0.85), (x - r * 0.85, y + r * 0.15)]
        self.draw_body(surface, pts)
        draw_aa_circle(surface, WHITE, (int(x - r * 0.25), int(y - r * 0.1)), 3)
        draw_aa_circle(surface, WHITE, (int(x + r * 0.25), int(y - r * 0.1)), 3)


# ---- 7. Ion Fortress: near-stationary, rotating bullet spokes + missile barrage ----
class Boss7_IonFortress(Boss):
    NAME = "ION FORTRESS"
    COLOR = RED

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2
        self.y = self.target_y + math.sin(self.t * 0.5) * 12
        self._spoke_ang = getattr(self, "_spoke_ang", 0.0) + dt * (0.9 if self.phase < 2 else 1.4)
        self._cd = getattr(self, "_cd", 0.4) - dt
        if self._cd <= 0:
            self._cd = 0.35 if self.phase == 0 else (0.25 if self.phase == 1 else 0.18)
            spokes = 6
            for i in range(spokes):
                ang = self._spoke_ang + i * math.tau / spokes
                bullets_out.append(EnemyBullet(self.x, self.y, math.cos(ang), math.sin(ang), speed=200, color=self.COLOR, radius=5))
        self._barrage_cd = getattr(self, "_barrage_cd", 4.0) - dt
        if self._barrage_cd <= 0:
            self._barrage_cd = 4.2 if self.phase < 2 else 3.0
            for ox in (-50, 0, 50):
                dx, dy = (player.x - (self.x + ox)), (player.y - self.y)
                bullets_out.append(EnemyBullet(self.x + ox, self.y + 20, dx, dy, speed=310, color=ORANGE, radius=7))

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        col = flash_color(self.COLOR, self.hit_flash)
        spoke_ang = getattr(self, "_spoke_ang", 0.0)
        for i in range(6):
            ang = spoke_ang + i * math.tau / 6
            ex, ey = x + math.cos(ang) * r * 1.4, y + math.sin(ang) * r * 1.4
            pygame.draw.line(surface, col, (x, y), (ex, ey), 3)
        pts = [(x + r * 0.8 * math.cos(math.tau * i / 8), y + r * 0.8 * math.sin(math.tau * i / 8)) for i in range(8)]
        self.draw_body(surface, pts)
        draw_glow_circle(surface, (int(x), int(y)), 14, WHITE, layers=2, base_alpha=90)


# ---- 8. Phase Reaper: teleports, fires aimed shotgun blast after each blink ----
class Boss8_PhaseReaper(Boss):
    NAME = "PHASE REAPER"
    COLOR = (210, 90, 255)

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self._tp_cd = getattr(self, "_tp_cd", 1.8) - dt
        if self._tp_cd <= 0:
            self._tp_cd = 1.8 if self.phase == 0 else (1.3 if self.phase == 1 else 0.95)
            spawn_burst(particles, self.x, self.y, self.COLOR, count=18, speed=(80, 260))
            self.x = random.uniform(90, WIDTH - 90)
            self.y = random.uniform(140, 300)
            spawn_burst(particles, self.x, self.y, self.COLOR, count=18, speed=(80, 260))
            base = math.atan2(player.y - self.y, player.x - self.x)
            count = 7 if self.phase < 2 else 10
            for i in range(count):
                ang = base + (i - count // 2) * 0.11
                bullets_out.append(EnemyBullet(self.x, self.y, math.cos(ang), math.sin(ang), speed=280, color=self.COLOR))
            play_sound("hit")

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        pts = []
        for i in range(10):
            ang = math.tau * i / 10 - math.pi / 2
            rr = r * (1.25 if i % 2 == 0 else 0.5)
            pts.append((x + rr * math.cos(ang), y + rr * math.sin(ang) * 1.15))
        self.draw_body(surface, pts)
        draw_aa_circle(surface, (30, 10, 40), (int(x), int(y)), int(r * 0.3))


# ---- 9. Nova Harbinger: alternates radial novas and homing-missile waves ----
class Boss9_NovaHarbinger(Boss):
    NAME = "NOVA HARBINGER"
    COLOR = YELLOW

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.7) * 150
        self._nova_cd = getattr(self, "_nova_cd", 2.2) - dt
        self._homing_cd = getattr(self, "_homing_cd", 3.4) - dt
        if self._nova_cd <= 0:
            self._nova_cd = 2.4 if self.phase == 0 else (1.7 if self.phase == 1 else 1.0)
            self._ring_shot(bullets_out, count=16 if self.phase < 2 else 22, speed=210)
            play_sound("hit")
        if self._homing_cd <= 0:
            self._homing_cd = 3.6 if self.phase < 2 else 2.4
            for _ in range(3):
                dx, dy = (player.x - self.x) + random.uniform(-60, 60), (player.y - self.y) + random.uniform(-40, 40)
                bullets_out.append(EnemyBullet(self.x, self.y, dx, dy, speed=180, color=ORANGE, radius=7))

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        col = flash_color(self.COLOR, self.hit_flash)
        pts = []
        for i in range(16):
            ang = self.t * 0.6 + math.tau * i / 16
            rr = r * (1.15 if i % 2 == 0 else 0.6)
            pts.append((x + rr * math.cos(ang), y + rr * math.sin(ang)))
        self.draw_body(surface, pts)
        draw_glow_circle(surface, (int(x), int(y)), 16, WHITE, layers=3, base_alpha=110)


# ---- 10. The Singularity (final boss): combines spiral + laser + minions + nova across phases ----
class Boss10_Singularity(Boss):
    NAME = "THE SINGULARITY"
    COLOR = WHITE

    def __init__(self, stage):
        super().__init__(stage)
        self.hp = self.max_hp = 90 + stage * 34 + 260
        self.lasers = []

    def attack_update(self, dt, player, bullets_out, particles, enemies_out):
        self.x = WIDTH / 2 + math.sin(self.t * 0.5) * 130
        self.y = self.target_y + math.sin(self.t * 1.1) * 18

        self._spiral_cd = getattr(self, "_spiral_cd", 0.1) - dt
        if self._spiral_cd <= 0:
            self._spiral_cd = 0.08
            self._spiral_ang = getattr(self, "_spiral_ang", 0.0) + 0.5
            for k in range(2):
                ang = self._spiral_ang + k * math.pi
                bullets_out.append(EnemyBullet(self.x, self.y, math.cos(ang), math.sin(ang), speed=200, color=PURPLE, radius=5))

        if self.phase >= 1:
            self._laser_cd = getattr(self, "_laser_cd", 3.0) - dt
            if self._laser_cd <= 0:
                self._laser_cd = 2.6 if self.phase == 1 else 1.8
                lx = random.uniform(80, WIDTH - 80)
                self.lasers.append(BossLaser(lx, width=52))

        if self.phase >= 1:
            self._spawn_cd = getattr(self, "_spawn_cd", 5.0) - dt
            if self._spawn_cd <= 0:
                self._spawn_cd = 5.5 if self.phase == 1 else 3.5
                enemies_out.append(Enemy(self.x - 60, self.y + 30, "zigzag", tier=self.stage))
                enemies_out.append(Enemy(self.x + 60, self.y + 30, "zigzag", tier=self.stage))

        if self.phase == 2:
            self._nova_cd = getattr(self, "_nova_cd", 2.0) - dt
            if self._nova_cd <= 0:
                self._nova_cd = 1.6
                self._ring_shot(bullets_out, count=20, speed=220, color=WHITE)
                play_sound("hit")

        for laser in self.lasers:
            laser.update(dt)
            if laser.state == "firing" and not laser.hit_applied and laser.rect().collidepoint(player.x, player.y):
                laser.hit_applied = True
                player.take_hit(1.8, tier=self.stage)
        self.lasers = [l for l in self.lasers if l.state != "done"]

    def draw_hull(self, surface):
        x, y, r = self.x, self.y, self.radius
        for i, (rad, col, a) in enumerate([
            (r * 1.35, PURPLE, 60), (r * 1.05, BLUE, 90), (r * 0.7, MAGENTA, 130),
        ]):
            ang = self.t * (1.2 + i * 0.6)
            s = pygame.Surface((int(rad * 2.4), int(rad * 2.4)), pygame.SRCALPHA)
            c = s.get_width() / 2
            pygame.draw.circle(s, (*col, a), (int(c), int(c)), int(rad), width=4)
            # small orbiting nodes to sell the "swirling" motion
            nx, ny = c + math.cos(ang) * rad, c + math.sin(ang) * rad
            draw_aa_circle(s, (*WHITE, min(255, a + 80)), (int(nx), int(ny)), 4)
            surface.blit(s, (x - c, y - c), special_flags=pygame.BLEND_RGBA_ADD)
        draw_glow_circle(surface, (int(x), int(y)), int(r * 0.4), flash_color(WHITE, self.hit_flash), layers=4, base_alpha=140)

    def draw(self, surface):
        for laser in self.lasers:
            laser.draw(surface)
        super().draw(surface)


BOSS_CLASSES = [
    Boss1_ReconDrone,
    Boss2_TwinTurret,
    Boss3_PlasmaInterceptor,
    Boss4_SwarmCarrier,
    Boss5_LaserFrigate,
    Boss6_VoidSerpent,
    Boss7_IonFortress,
    Boss8_PhaseReaper,
    Boss9_NovaHarbinger,
    Boss10_Singularity,
]

# --------------------------------------------------------------------------
# STAGE / WAVE DEFINITIONS
# --------------------------------------------------------------------------
NUM_STAGES = 10


def enemy_kinds_for_stage(stage):
    kinds = ["drone"]
    if stage >= 2:
        kinds.append("zigzag")
    if stage >= 3:
        kinds.append("kamikaze")
    if stage >= 5:
        kinds.append("turret")
    if stage >= 7:
        kinds.append("shielded")
    return kinds


def build_wave_plan(stage, endless_wave=None):
    """Returns a list of (spawn_time, kind, x) tuples for the pre-boss portion
    of a stage (or one endless wave). Both the number of waves and the time
    between them scale up with the stage number, so the boss shows up later
    and later on deeper stages instead of a fixed short runway. Waves are
    spaced tightly and each wave is bigger, so there's rarely dead air
    between them."""
    tier = stage if endless_wave is None else NUM_STAGES
    kinds = enemy_kinds_for_stage(min(tier, NUM_STAGES))
    plan = []
    t = 1.5
    if endless_wave is None:
        waves = 16 + stage * 3  # stage 1 -> 19 waves, stage 10 -> 46 waves
    else:
        waves = 48 + endless_wave * 6  # keeps growing each endless loop
    for w in range(waves):
        row = random.randint(4, 6)
        kind = random.choice(kinds)
        spacing = WIDTH / (row + 1)
        for i in range(row):
            plan.append((t, kind, spacing * (i + 1)))
        t += random.uniform(2.0, 2.8)
    boss_buffer = random.uniform(6.0, 9.0)
    return plan, t + boss_buffer


# --------------------------------------------------------------------------
# GAME STATES
# --------------------------------------------------------------------------
STATE_MENU = "menu"
STATE_READY = "ready"
STATE_PLAY = "play"
STATE_PAUSED = "paused"
STATE_BOSS_INTRO = "boss_intro"
STATE_STAGE_CLEAR = "stage_clear"
STATE_GAME_OVER = "game_over"
STATE_VICTORY = "victory"
STATE_HIGHSCORES = "highscores"
STATE_STAGE_SELECT = "stage_select"

PAUSE_ICON_RECT = pygame.Rect(16, 16, 34, 34)

# --------------------------------------------------------------------------
# MAIN GAME CLASS
# --------------------------------------------------------------------------
POWERUP_DROP_CHANCE = 0.05


class Game:
    def __init__(self):
        self.state = STATE_MENU
        self.stage = 1
        self.endless = False
        self.endless_loop = 0
        self.player = Player()
        self.bullets = []
        self.missiles = []
        self.enemy_bullets = []
        self.enemies = []
        self.boss = None
        self.powerups = []
        self.particles = []
        self.shockwaves = []
        self.popups = []
        self.flash_alpha = 0.0
        self.wave_plan = []
        self.wave_plan_index = 0
        self.stage_timer = 0.0
        self.boss_spawn_time = 999.0
        self.ready_timer = 0.0
        self.boss_intro_timer = 0.0
        self.powerup_spawn_cd = random.uniform(18, 26)
        self.shake_time_left = 0.0
        self.shake_duration_total = 0.3
        self.shake_magnitude = 0.0
        self.menu_t = 0.0
        self._geometry_dirty = False
        self._geometry_save_accum = 0.0
        self.last_result_stage = 1
        self.highscores_cache = load_highscores()
        self._missile_tap_pending = False
        self.resume_cooldown = 0.0
        self._score_finalized = False
        self._run_id = uuid.uuid4().hex
        self._last_autosaved_score = 0
        self._autosave_accum = 0.0

    # ---------------- lifecycle ----------------
    def _begin_new_run(self):
        """Closes out whatever run was previously in progress (saving it if it hadn't
        already been saved via death/victory/quit) and resets autosave tracking for
        the run about to start."""
        self.finalize_score()
        self._score_finalized = False
        self._run_id = uuid.uuid4().hex
        self._last_autosaved_score = 0
        self._autosave_accum = 0.0

    def reset_full(self):
        self._begin_new_run()
        self.stage = 1
        self.endless = False
        self.endless_loop = 0
        self.player = Player()
        self.particles = []
        self.shockwaves = []
        self.popups = []
        self.load_stage(1)
        set_cursor_locked(True)

    def continue_game(self):
        self._begin_new_run()
        self.stage = min(NUM_STAGES, max(1, CONFIG.get("max_stage_reached", 1)))
        self.endless = False
        self.endless_loop = 0
        self.player = Player()
        self.particles = []
        self.shockwaves = []
        self.popups = []
        self.load_stage(self.stage)
        set_cursor_locked(True)

    def start_at_stage(self, n):
        self._begin_new_run()
        self.stage = max(1, min(NUM_STAGES, n))
        self.endless = False
        self.endless_loop = 0
        self.player = Player()
        self.particles = []
        self.shockwaves = []
        self.popups = []
        self.load_stage(self.stage)
        set_cursor_locked(True)

    def start_endless(self):
        self._begin_new_run()
        self.stage = NUM_STAGES
        self.endless = True
        self.endless_loop = 0
        self.player = Player()
        self.particles = []
        self.shockwaves = []
        self.popups = []
        self.load_stage(self.stage, endless=True)
        set_cursor_locked(True)

    def restart_stage(self):
        self._begin_new_run()
        self.player = Player()
        self.load_stage(self.stage, endless=self.endless)
        set_cursor_locked(True)

    def load_stage(self, n, endless=False):
        self.stage = n
        self.endless = endless
        self.resume_cooldown = 0.0
        effective_tier = NUM_STAGES + self.endless_loop if endless else n
        self.player.set_tier(effective_tier)
        self.wave_plan, boss_time = build_wave_plan(n, endless_wave=self.endless_loop if endless else None)
        self.wave_plan_index = 0
        self.stage_timer = 0.0
        self.boss_spawn_time = boss_time
        self.boss = None
        self.enemies = []
        self.bullets = []
        self.missiles = []
        self.enemy_bullets = []
        self.powerups = []
        self.powerup_spawn_cd = random.uniform(18, 26)
        self.state = STATE_READY
        self.ready_timer = 1.3

    def next_stage(self):
        if self.endless:
            self.endless_loop += 1
            self.load_stage(NUM_STAGES, endless=True)
        elif self.stage >= NUM_STAGES:
            self.state = STATE_VICTORY
        else:
            self.load_stage(self.stage + 1)

    def finalize_score(self):
        """Call any time a run ends or is abandoned (game over, victory, quitting,
        restarting, or returning to the menu mid-stage/mid-endless) to persist the
        score for good. Idempotent per run - safe to call more than once (e.g. both
        on death and on a subsequent quit)."""
        if getattr(self, "_score_finalized", False) or self.player.score <= 0:
            return
        self._score_finalized = True
        stage_for_score = NUM_STAGES + self.endless_loop if self.endless else self.stage
        self.highscores_cache = save_highscore(
            self.player.score, stage_for_score, endless=self.endless, run_id=self._run_id
        )

    def _autosave_tick(self, dt):
        """Persists the current run's score to disk every few seconds while it's still
        in progress, so a hard crash or forced kill doesn't lose it - not just the
        explicit end-of-run/quit paths that finalize_score() already covers."""
        if (
            getattr(self, "_score_finalized", False)
            or self.player.score <= 0
            or self.player.score == self._last_autosaved_score
        ):
            return
        self._autosave_accum += dt
        if self._autosave_accum < 3.0:
            return
        self._autosave_accum = 0.0
        self._last_autosaved_score = self.player.score
        stage_for_score = NUM_STAGES + self.endless_loop if self.endless else self.stage
        self.highscores_cache = save_highscore(
            self.player.score, stage_for_score, endless=self.endless, run_id=self._run_id
        )

    def quit_game(self):
        self.finalize_score()
        if getattr(self, "_geometry_dirty", False):
            save_config(CONFIG)
        log("Quitting.")
        pygame.quit()
        sys.exit()

    def toggle_pause(self):
        if self.state == STATE_PLAY or self.state == STATE_BOSS_INTRO or self.state == STATE_READY:
            self._pre_pause_state = self.state
            self.state = STATE_PAUSED
            set_cursor_locked(False)
            play_sound("ui_click")
        elif self.state == STATE_PAUSED:
            self.state = getattr(self, "_pre_pause_state", STATE_PLAY)
            set_cursor_locked(True)
            self.resume_cooldown = 3.0
            play_sound("countdown_tick")

    def add_shake(self, magnitude, duration=0.3):
        self.shake_magnitude = max(self.shake_magnitude, magnitude)
        self.shake_time_left = max(self.shake_time_left, duration)
        self.shake_duration_total = duration

    def add_shockwave(self, x, y, color, max_radius=90, life=0.4):
        self.shockwaves.append(Shockwave(x, y, color, max_radius=max_radius, life=life))

    def add_flash(self, alpha):
        self.flash_alpha = max(self.flash_alpha, alpha)

    def add_popup(self, x, y, text, color, life=0.8):
        self.popups.append(FloatingText(x, y - 10, text, color, life=life))

    # ---------------- boss/stage resolution ----------------
    def _spawn_boss(self):
        if self.endless:
            cls = random.choice(BOSS_CLASSES)
            effective_stage = NUM_STAGES + self.endless_loop
        else:
            cls = BOSS_CLASSES[self.stage - 1]
            effective_stage = self.stage
        self.boss = cls(effective_stage)
        self.state = STATE_BOSS_INTRO
        self.boss_intro_timer = 1.8
        play_sound("boss_alert")

    def _on_boss_defeated(self):
        assert self.boss is not None
        spawn_burst(self.particles, self.boss.x, self.boss.y, self.boss.COLOR, count=60, speed=(80, 420), life=(0.4, 1.0), size=5)
        self.add_shockwave(self.boss.x, self.boss.y, self.boss.COLOR, max_radius=150, life=0.6)
        self.add_flash(150)
        play_sound("explosion_big")
        self.add_shake(10, 0.5)
        self.add_popup(self.boss.x, self.boss.y, f"+{self.boss.score_value}", YELLOW, life=1.2)
        self.player.score += self.boss.score_value
        self.boss = None

        stage_for_score = NUM_STAGES + self.endless_loop if self.endless else self.stage
        self.last_result_stage = stage_for_score

        if self.endless:
            play_sound("stage_clear")
            self.next_stage()
            return

        CONFIG["max_stage_reached"] = max(CONFIG.get("max_stage_reached", 1), min(NUM_STAGES, self.stage + 1))
        if self.stage >= NUM_STAGES:
            CONFIG["game_completed"] = True
            save_config(CONFIG)
            self.finalize_score()
            self.state = STATE_VICTORY
            set_cursor_locked(False)
            play_sound("stage_clear")
        else:
            save_config(CONFIG)
            self.state = STATE_STAGE_CLEAR
            set_cursor_locked(False)
            play_sound("stage_clear")

    def _on_player_died(self):
        self.add_shockwave(self.player.x, self.player.y, RED, max_radius=120, life=0.5)
        self.add_flash(120)
        play_sound("explosion_big")
        self.add_shake(14, 0.6)
        self.finalize_score()
        self.state = STATE_GAME_OVER
        set_cursor_locked(False)

    def _eligible_powerup_kinds(self):
        """Filters out 'life' once hearts are already at the cap. 'life' is allowed
        back into Endless Mode, but stays a special/uncommon treat there rather than
        a regular drop - see the endless weight override in _pick_powerup_kind.
        Stops any kind from being offered twice - whether a matching pickup is
        already sitting on screen, or its effect is already active on the player
        (so e.g. two missile-overcharge pickups can't be up/active at once). Also
        permanently retires the triple/quint pickups once their effect
        becomes the stage's baseline (triple from stage 5 on, quint from
        stage 8 on) - there's nothing left for them to upgrade to."""
        p = self.player
        onscreen = {pu.kind for pu in self.powerups if not pu.dead}
        kinds = list(POWERUP_INFO.keys())
        if p.hearts >= MAX_HEARTS:
            kinds = [k for k in kinds if k != "life"]
        if p.base_spread_level >= 1:
            kinds = [k for k in kinds if k != "triple"]
        if p.base_spread_level >= 2:
            kinds = [k for k in kinds if k != "quint"]
        if (onscreen & {"triple", "quint"}) or p.spread_level > p.base_spread_level:
            kinds = [k for k in kinds if k not in ("triple", "quint")]
        if "missile_overcharge" in onscreen or p.missile_overcharge_timer > 0:
            kinds = [k for k in kinds if k != "missile_overcharge"]
        if "shield" in onscreen or p.shield_timer > 0:
            kinds = [k for k in kinds if k != "shield"]
        return kinds

    # In Endless Mode 'life' pickups should feel like a rare, special treat rather
    # than a regular drop - this weight is used in place of POWERUP_INFO's normal
    # weight (3) only while self.endless is True.
    ENDLESS_LIFE_RARITY_WEIGHT = 0.4

    def _pick_powerup_kind(self):
        kinds = self._eligible_powerup_kinds()
        if not kinds:
            return None
        weights = [
            self.ENDLESS_LIFE_RARITY_WEIGHT if (k == "life" and self.endless) else POWERUP_INFO[k][2]
            for k in kinds
        ]
        return random.choices(kinds, weights=weights, k=1)[0]

    def _spawn_thruster_particles(self):
        """Continuous small exhaust particles from both engines, replacing the
        old static glow-only thruster look. Skipped during the spawn-invuln
        blink frame so it doesn't emit while the ship itself is hidden."""
        p = self.player
        if p.invuln_timer > 0 and int(p.invuln_timer * 12) % 2 == 0:
            return
        for ox in (-9, 9):
            if random.random() < 0.85:
                ang = math.pi / 2 + random.uniform(-0.3, 0.3)
                spd = random.uniform(70, 160)
                self.particles.append(
                    Particle(
                        p.x + ox, p.y + 19,
                        math.cos(ang) * spd, math.sin(ang) * spd,
                        random.uniform(0.15, 0.3), ORANGE, size=random.randint(2, 3),
                    )
                )

    def _maybe_drop_powerup(self, x, y):
        if random.random() > POWERUP_DROP_CHANCE:
            return
        kind = self._pick_powerup_kind()
        if kind is not None:
            self.powerups.append(PowerUp(x, y, kind))

    # ---------------- update ----------------
    def update(self, dt, keys, mouse_pos, mouse_moved):
        self._autosave_tick(dt)
        self.menu_t += dt
        if self.shake_time_left > 0:
            self.shake_time_left -= dt

        for p in self.particles:
            p.update(dt)
        self.particles = [p for p in self.particles if p.life > 0]

        self.shockwaves = [sw for sw in self.shockwaves if sw.update(dt)]
        self.popups = [fp for fp in self.popups if fp.update(dt)]
        if self.flash_alpha > 0:
            self.flash_alpha = max(0.0, self.flash_alpha - dt * 900)

        if self.state == STATE_MENU:
            update_draw_starfield(screen, dt, 0.4)
            return
        if self.state == STATE_HIGHSCORES or self.state == STATE_STAGE_SELECT:
            update_draw_starfield(screen, dt, 0.4)
            return
        if self.state == STATE_PAUSED:
            return
        if self.state == STATE_GAME_OVER or self.state == STATE_VICTORY or self.state == STATE_STAGE_CLEAR:
            update_draw_starfield(screen, dt, 0.5)
            return

        if self.resume_cooldown > 0 and self.state in (STATE_PLAY, STATE_READY, STATE_BOSS_INTRO):
            update_draw_starfield(screen, dt, 0.5)
            prev_ceil = math.ceil(self.resume_cooldown)
            self.resume_cooldown = max(0.0, self.resume_cooldown - dt)
            new_ceil = math.ceil(self.resume_cooldown)
            if self.resume_cooldown <= 0:
                play_sound("countdown_go")
            elif new_ceil != prev_ceil:
                play_sound("countdown_tick")
            return

        if self.state == STATE_READY:
            update_draw_starfield(screen, dt, 0.7)
            self.ready_timer -= dt
            if self.ready_timer <= 0:
                self.state = STATE_PLAY
                set_cursor_locked(True)
            return

        if self.state == STATE_BOSS_INTRO:
            update_draw_starfield(screen, dt, 0.9)
            self.boss_intro_timer -= dt
            if self.boss.entering is False and self.boss_intro_timer <= 0.6:
                pass
            self.boss.update(dt, self.player, self.enemy_bullets, self.particles, self.enemies)
            if self.boss_intro_timer <= 0:
                self.state = STATE_PLAY
            return

        if self.state == STATE_PLAY:
            self.update_play(dt, keys, mouse_pos, mouse_moved)

    def update_play(self, dt, keys, mouse_pos, mouse_moved):
        speed_mult = 1.0 if self.boss is None else 1.2
        update_draw_starfield(screen, dt, speed_mult)

        self.stage_timer += dt

        while self.wave_plan_index < len(self.wave_plan) and self.wave_plan[self.wave_plan_index][0] <= self.stage_timer:
            _, kind, x = self.wave_plan[self.wave_plan_index]
            tier = NUM_STAGES + self.endless_loop if self.endless else self.stage
            self.enemies.append(Enemy(x, -30, kind, tier=tier))
            self.wave_plan_index += 1

        if (
            self.boss is None
            and self.wave_plan_index >= len(self.wave_plan)
            and self.stage_timer >= self.boss_spawn_time
        ):
            self._spawn_boss()
            return

        mouse_left = pygame.mouse.get_pressed(3)[0]
        firing_primary = bool(keys[pygame.K_SPACE] or mouse_left)
        missile_tap = self._missile_tap_pending
        self._missile_tap_pending = False

        new_bullets, new_missiles = self.player.update(dt, keys, mouse_pos, mouse_moved, firing_primary, missile_tap)
        self.bullets.extend(new_bullets)
        self.missiles.extend(new_missiles)
        self._spawn_thruster_particles()

        for b in self.bullets:
            b.update(dt)
        self.bullets = [b for b in self.bullets if not b.dead]

        target_pool = list(self.enemies)
        if self.boss is not None:
            target_pool.append(self.boss)
        for m in self.missiles:
            m.update(dt, target_pool)
        self.missiles = [m for m in self.missiles if not m.dead]

        for eb in self.enemy_bullets:
            eb.update(dt)
        self.enemy_bullets = [eb for eb in self.enemy_bullets if not eb.dead]

        for e in self.enemies:
            e.update(dt, self.player, self.enemy_bullets)
        self.enemies = [e for e in self.enemies if not e.dead]

        if self.boss is not None:
            self.boss.update(dt, self.player, self.enemy_bullets, self.particles, self.enemies)

        for pu in self.powerups:
            pu.update(dt)
        self.powerups = [pu for pu in self.powerups if not pu.dead]

        self.powerup_spawn_cd -= dt
        if self.powerup_spawn_cd <= 0:
            self.powerup_spawn_cd = random.uniform(22, 32)
            kind = self._pick_powerup_kind()
            if kind is not None:
                self.powerups.append(PowerUp(random.uniform(60, WIDTH - 60), -20, kind))

        self._resolve_collisions()

        if self.player.lives <= 0 and self.state == STATE_PLAY:
            self._on_player_died()

    def _resolve_collisions(self):
        player = self.player
        tier = NUM_STAGES + self.endless_loop if self.endless else self.stage

        # player bullets vs enemies
        for b in self.bullets:
            if b.dead:
                continue
            for e in self.enemies:
                if e.dead:
                    continue
                if math.hypot(b.x - e.x, b.y - e.y) <= b.radius + e.radius:
                    b.dead = True
                    if e.hit(b.damage):
                        player.score += e.score_value
                        spawn_burst(self.particles, e.x, e.y, e.color, count=16, size=4)
                        self.add_shockwave(e.x, e.y, e.color, max_radius=42, life=0.25)
                        self.add_popup(e.x, e.y, f"+{e.score_value}", WHITE, life=0.6)
                        play_sound("explosion")
                        self._maybe_drop_powerup(e.x, e.y)
                    break

        # player bullets vs boss
        if self.boss is not None and not self.boss.entering:
            for b in self.bullets:
                if b.dead:
                    continue
                if math.hypot(b.x - self.boss.x, b.y - self.boss.y) <= b.radius + self.boss.radius:
                    b.dead = True
                    if self.boss.hit(b.damage):
                        self._on_boss_defeated()
                        return
                    spawn_burst(self.particles, b.x, b.y, WHITE, count=6, speed=(40, 140), life=(0.15, 0.3), size=3)
                    play_sound("hit")

        # player missiles vs enemies
        for m in self.missiles:
            if m.dead:
                continue
            for e in self.enemies:
                if e.dead:
                    continue
                if math.hypot(m.x - e.x, m.y - e.y) <= m.radius + e.radius:
                    m.dead = True
                    if e.hit(m.damage):
                        player.score += e.score_value
                        spawn_burst(self.particles, e.x, e.y, e.color, count=20, size=5)
                        self.add_shockwave(e.x, e.y, e.color, max_radius=52, life=0.3)
                        self.add_popup(e.x, e.y, f"+{e.score_value}", WHITE, life=0.6)
                        play_sound("explosion")
                        self._maybe_drop_powerup(e.x, e.y)
                    break

        # player missiles vs boss
        if self.boss is not None and not self.boss.entering:
            for m in self.missiles:
                if m.dead:
                    continue
                if math.hypot(m.x - self.boss.x, m.y - self.boss.y) <= m.radius + self.boss.radius:
                    m.dead = True
                    spawn_burst(self.particles, m.x, m.y, ORANGE, count=10, size=4)
                    if self.boss.hit(m.damage):
                        self._on_boss_defeated()
                        return
                    self.add_shake(3, 0.12)
                    play_sound("hit")

        # enemy bullets vs player
        for eb in self.enemy_bullets:
            if eb.dead:
                continue
            if math.hypot(eb.x - player.x, eb.y - player.y) <= eb.radius + player.radius:
                eb.dead = True
                if player.take_hit(eb.damage, tier=tier):
                    spawn_burst(self.particles, player.x, player.y, RED, count=14, size=4)
                    self.add_shake(6, 0.25)
                    if player.lives <= 0:
                        return

        # enemy bodies vs player (contact damage)
        for e in self.enemies:
            if e.dead:
                continue
            if math.hypot(e.x - player.x, e.y - player.y) <= e.radius + player.radius:
                e.dead = True
                spawn_burst(self.particles, e.x, e.y, e.color, count=14, size=4)
                if player.take_hit(1.2, tier=tier):
                    self.add_shake(6, 0.25)
                    if player.lives <= 0:
                        return

        # boss body vs player (contact damage, gentler - bosses are big)
        if self.boss is not None and not self.boss.entering:
            if math.hypot(self.boss.x - player.x, self.boss.y - player.y) <= self.boss.radius * 0.7 + player.radius:
                if player.take_hit(1.5, tier=tier):
                    self.add_shake(8, 0.3)
                    if player.lives <= 0:
                        return

        # powerups vs player
        for pu in self.powerups:
            if pu.dead:
                continue
            if math.hypot(pu.x - player.x, pu.y - player.y) <= POWERUP_SIZE / 2 + player.radius:
                pu.dead = True
                player.apply_powerup(pu.kind)
                _, pu_color, _ = POWERUP_INFO[pu.kind]
                self.add_popup(pu.x, pu.y, POWERUP_LABELS.get(pu.kind, "!"), pu_color, life=0.9)
                play_sound("powerup")
                if pu.kind == "shield":
                    play_sound("shield")

    # ---------------- drawing ----------------
    def draw_hud(self, surface):
        draw_text_center(surface, f"SCORE {self.player.score}", FONT_TINY, WHITE, (WIDTH - 80, 30))
        label = "ENDLESS" if self.endless else f"STAGE {self.stage}/{NUM_STAGES}"
        draw_text_center(surface, label, FONT_TINY, CYAN, (WIDTH / 2, 30))

        # heart icons: a soft glow directly around a bold triangle - no solid
        # background chip, so nothing square-shaped is visible, just the heart.
        # At zero hearts there'd be nothing to show at all, so a blinking "LIVES"
        # label takes their place as a last-chance warning.
        size, gap = 34, 8
        start_x = PAUSE_ICON_RECT.right + 14
        hearts_shown = max(0, self.player.hearts - 1)
        if hearts_shown == 0:
            label_color = heartbeat_color(self.menu_t)
            draw_text_center(surface, "LIVES", FONT_TINY, label_color, (start_x + 34, PAUSE_ICON_RECT.centery))
        else:
            for i in range(hearts_shown):
                cx = start_x + i * (size + gap) + size / 2
                cy = PAUSE_ICON_RECT.centery
                pts = [(cx, cy - 13), (cx + 12, cy + 10), (cx - 12, cy + 10)]
                draw_glow_poly(surface, pts, CYAN, layers=3, expand=2, base_alpha=70)

    def draw_pause_icon(self, surface):
        r = PAUSE_ICON_RECT
        draw_glow_rect(surface, r, GRAY, layers=2, base_alpha=40, radius=6)
        pygame.draw.rect(surface, WHITE, (r.x + 9, r.y + 7, 5, 20))
        pygame.draw.rect(surface, WHITE, (r.x + 20, r.y + 7, 5, 20))

    def draw(self):
        if self.state == STATE_MENU:
            self.draw_menu(screen)
        elif self.state == STATE_HIGHSCORES:
            self.draw_highscores(screen)
        elif self.state == STATE_STAGE_SELECT:
            self.draw_stage_select(screen)
        elif self.state in (STATE_READY, STATE_PLAY, STATE_BOSS_INTRO, STATE_PAUSED):
            self.draw_play(screen)
            if self.state == STATE_READY:
                self.draw_ready_overlay(screen)
            elif self.state == STATE_BOSS_INTRO:
                self.draw_boss_intro_overlay(screen)
            elif self.state == STATE_PAUSED:
                self.draw_pause_overlay(screen)
            if self.resume_cooldown > 0 and self.state != STATE_PAUSED:
                self.draw_resume_cooldown_overlay(screen)
        elif self.state == STATE_STAGE_CLEAR:
            self.draw_play(screen)
            self.draw_stage_clear_overlay(screen)
        elif self.state == STATE_GAME_OVER:
            self.draw_play(screen)
            self.draw_game_over_overlay(screen)
        elif self.state == STATE_VICTORY:
            self.draw_play(screen)
            self.draw_victory_overlay(screen)

    def draw_play(self, surface):
        for pu in self.powerups:
            pu.draw(surface)
        for eb in self.enemy_bullets:
            eb.draw(surface)
        for e in self.enemies:
            e.draw(surface)
        if self.boss is not None:
            self.boss.draw(surface)
        for b in self.bullets:
            b.draw(surface)
        for m in self.missiles:
            m.draw(surface)
        self.player.draw(surface)
        for p in self.particles:
            p.draw(surface)
        for sw in self.shockwaves:
            sw.draw(surface)
        for fp in self.popups:
            fp.draw(surface)
        self.draw_hud(surface)
        self.draw_pause_icon(surface)
        self.draw_active_boosters(surface)
        if self.flash_alpha > 0:
            flash = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
            flash.fill((255, 255, 255, int(self.flash_alpha)))
            surface.blit(flash, (0, 0))

    def draw_active_boosters(self, surface):
        p = self.player
        active = []
        if p.spread_level == 1 and p.spread_timer > 0:
            active.append(("TRIPLE SHOT", p.spread_timer, CYAN))
        elif p.spread_level == 2 and p.spread_timer > 0:
            active.append(("QUINTUPLE SHOT", p.spread_timer, MAGENTA))
        if p.missile_overcharge_timer > 0:
            active.append(("MISSILE OVERCHARGE", p.missile_overcharge_timer, ORANGE))
        if p.shield_timer > 0:
            active.append(("SHIELD", p.shield_timer, BLUE))
        if not active:
            return
        y = HEIGHT - 20 - (len(active) - 1) * 22
        for label, remaining, color in active:
            draw_text_center(surface, f"{label}  {remaining:0.1f}s", FONT_TINY, color, (WIDTH / 2, y))
            y += 22

    def draw_ready_overlay(self, surface):
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((0, 0, 0, 90))
        surface.blit(s, (0, 0))
        label = "ENDLESS MODE" if self.endless else f"STAGE {self.stage}"
        draw_text_center(surface, label, FONT_MED, WHITE, (WIDTH / 2, HEIGHT / 2 - 20), glow=CYAN)
        draw_text_center(surface, "GET READY", FONT_SMALL, CYAN, (WIDTH / 2, HEIGHT / 2 + 24))

    def draw_boss_intro_overlay(self, surface):
        alpha = int(180 * min(1.0, self.boss_intro_timer / 1.8))
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((40, 0, 0, min(120, alpha)))
        surface.blit(s, (0, 0))
        draw_text_center(surface, "WARNING", FONT_MED, ALERT_RED, (WIDTH / 2, HEIGHT / 2 - 30), glow=RED)
        if self.boss is not None:
            draw_text_center(surface, self.boss.NAME, FONT_SMALL, WHITE, (WIDTH / 2, HEIGHT / 2 + 14))

    def draw_pause_overlay(self, surface):
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((0, 0, 0, 150))
        surface.blit(s, (0, 0))
        draw_text_center(surface, "PAUSED", FONT_BIG, WHITE, (WIDTH / 2, 220), glow=CYAN)
        for action, btn in self.build_pause_buttons():
            btn.draw(surface)

    def draw_resume_cooldown_overlay(self, surface):
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((0, 0, 0, 120))
        surface.blit(s, (0, 0))
        count = max(1, math.ceil(self.resume_cooldown))
        draw_text_center(surface, str(count), FONT_BIG, CYAN, (WIDTH / 2, HEIGHT / 2), glow=CYAN)
        draw_text_center(surface, "Get ready to move...", FONT_SMALL, WHITE, (WIDTH / 2, HEIGHT / 2 + 60))

    def draw_stage_clear_overlay(self, surface):
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((0, 0, 0, 140))
        surface.blit(s, (0, 0))
        draw_text_center(surface, "STAGE CLEAR", FONT_BIG, GREEN, (WIDTH / 2, HEIGHT / 2 - 80), glow=TEAL)
        draw_text_center(surface, f"Score: {self.player.score}", FONT_MED, WHITE, (WIDTH / 2, HEIGHT / 2 - 10))
        draw_text_center(surface, "Click or press SPACE to continue", FONT_SMALL, GRAY, (WIDTH / 2, HEIGHT / 2 + 60))

    def draw_game_over_overlay(self, surface):
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((30, 0, 0, 160))
        surface.blit(s, (0, 0))
        draw_text_center(surface, "GAME OVER", FONT_BIG, ALERT_RED, (WIDTH / 2, HEIGHT / 2 - 90), glow=RED)
        draw_text_center(surface, f"Score: {self.player.score}", FONT_MED, WHITE, (WIDTH / 2, HEIGHT / 2 - 20))
        label = f"Endless - Wave loop {self.endless_loop + 1}" if self.endless else f"Reached Stage {self.stage}"
        draw_text_center(surface, label, FONT_SMALL, GRAY, (WIDTH / 2, HEIGHT / 2 + 24))
        draw_text_center(surface, "Press R to restart, ESC for menu", FONT_SMALL, CYAN, (WIDTH / 2, HEIGHT / 2 + 70))

    def draw_victory_overlay(self, surface):
        s = pygame.Surface((WIDTH, HEIGHT), pygame.SRCALPHA)
        s.fill((0, 20, 30, 160))
        surface.blit(s, (0, 0))
        draw_text_center(surface, "VICTORY", FONT_BIG, YELLOW, (WIDTH / 2, HEIGHT / 2 - 110), glow=ORANGE)
        draw_text_center(surface, "The Singularity has fallen.", FONT_SMALL, WHITE, (WIDTH / 2, HEIGHT / 2 - 50))
        draw_text_center(surface, f"Final Score: {self.player.score}", FONT_MED, WHITE, (WIDTH / 2, HEIGHT / 2 + 6))
        draw_text_center(surface, "Stage Select & Endless Mode unlocked!", FONT_SMALL, CYAN, (WIDTH / 2, HEIGHT / 2 + 56))
        draw_text_center(surface, "Press R to restart, ESC for menu", FONT_SMALL, GRAY, (WIDTH / 2, HEIGHT / 2 + 96))

    # ---------------- menu / highscores / stage select ----------------
    def build_menu_buttons(self):
        buttons = []
        w, h, gap = WIDTH - 160, 56, 16
        x = 80
        y = 420
        buttons.append(("start", Button((x, y, w, h), "NEW GAME")))
        y += h + gap
        max_stage = CONFIG.get("max_stage_reached", 1)
        continue_label = f"CONTINUE (STAGE {max_stage})" if max_stage > 1 else "CONTINUE"
        buttons.append(("continue", Button((x, y, w, h), continue_label, enabled=max_stage > 1)))
        y += h + gap
        completed = CONFIG.get("game_completed", False)
        buttons.append(("stage_select", Button((x, y, w, h), "STAGE SELECT", enabled=completed)))
        y += h + gap
        buttons.append(("endless", Button((x, y, w, h), "ENDLESS MODE", enabled=completed)))
        y += h + gap
        buttons.append(("highscores", Button((x, y, w, h), "HIGH SCORES")))
        y += h + gap
        half = (w - gap) / 2
        buttons.append(("fullscreen", Button((x, y, half, h), "FULLSCREEN: " + ("ON" if CONFIG.get("fullscreen") else "OFF"))))
        buttons.append(("bgm", Button((x + half + gap, y, half, h), "MUSIC: " + ("ON" if BGM_ON else "OFF"))))
        y += h + gap
        buttons.append(("sfx", Button((x, y, half, h), "SFX: " + ("ON" if SFX_ON else "OFF"))))
        buttons.append(("quit", Button((x + half + gap, y, half, h), "QUIT")))
        return buttons

    def draw_menu_planet(self, surface):
        """Slow-drifting planet silhouette with a soft ring, giving the menu
        screen its own bit of motion beyond the shared starfield/nebula."""
        t = self.menu_t
        cx = WIDTH * 0.76 + math.sin(t * 0.06) * 16
        cy = 118 + math.cos(t * 0.045) * 10
        r = 62
        draw_glow_circle(surface, (int(cx), int(cy)), r + 12, PURPLE, layers=5, expand=4, base_alpha=32)
        body = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(body, r, r, r, (42, 32, 72, 235))
        pygame.gfxdraw.aacircle(body, r, r, r, (100, 80, 160, 255))
        shadow = pygame.Surface((r * 2, r * 2), pygame.SRCALPHA)
        pygame.gfxdraw.filled_circle(shadow, int(r * 1.35), r, r, (4, 6, 16, 150))
        body.blit(shadow, (0, 0))
        surface.blit(body, (cx - r, cy - r))
        ring_rect = pygame.Rect(0, 0, int(r * 2.5), int(r * 0.85))
        ring_rect.center = (int(cx), int(cy))
        ring = pygame.Surface((ring_rect.width, ring_rect.height), pygame.SRCALPHA)
        pygame.draw.ellipse(ring, (*CYAN, 90), ring.get_rect(), width=2)
        surface.blit(ring, ring_rect.topleft)

    def draw_menu(self, surface):
        self.draw_menu_planet(surface)
        draw_text_center(surface, "VOID", FONT_BIG, CYAN, (WIDTH / 2, 160), glow=BLUE)
        draw_text_center(surface, "BREAKER", FONT_BIG, WHITE, (WIDTH / 2, 220), glow=CYAN)
        draw_text_center(surface, "A Sci-Fi Space Shooter", FONT_SMALL, GRAY, (WIDTH / 2, 272))
        draw_text_center(surface, "10 Stages  -  10 Bosses  -  Endless Mode", FONT_TINY, GRAY, (WIDTH / 2, 298))

        # Build and draw menu buttons
        buttons = self.build_menu_buttons()
        for action, btn in buttons:
            btn.draw(surface)

        last_btn = buttons[-1][1]
        instructions_y = last_btn.rect.bottom + 28

        draw_text_center(
            surface,
            "Mouse to move  •  SPACE/Click to fire  •  SHIFT/Right-click for missile  •  P/ESC to pause",
            FONT_TINY,
            GRAY,
            (WIDTH / 2, instructions_y),
        )
        draw_text_center(
            surface,
            "Ctrl / Alt shows or hides the mouse cursor during play",
            FONT_TINY,
            GRAY,
            (WIDTH / 2, instructions_y + 26),
        )

        draw_text_center(
            surface,
            "made with \u2665 by Rane Kun",
            FONT_TINY,
            GRAY,
            (WIDTH / 2, HEIGHT - 24),
        )

    def handle_menu_action(self, action):
        if action == "start":
            self.reset_full()
        elif action == "continue":
            if CONFIG.get("max_stage_reached", 1) > 1:
                self.continue_game()
        elif action == "highscores":
            self.highscores_cache = load_highscores()
            self.state = STATE_HIGHSCORES
        elif action == "fullscreen":
            toggle_fullscreen()
        elif action == "bgm":
            set_bgm(not BGM_ON)
        elif action == "sfx":
            set_sfx(not SFX_ON)
        elif action == "stage_select":
            if CONFIG.get("game_completed", False):
                self.state = STATE_STAGE_SELECT
        elif action == "endless":
            if CONFIG.get("game_completed", False):
                self.start_endless()
        elif action == "quit":
            self.quit_game()
        play_sound("ui_click")

    def build_highscores_buttons(self):
        return [("back", Button((WIDTH / 2 - 100, HEIGHT - 120, 200, 54), "BACK"))]

    def draw_highscores(self, surface):
        draw_text_center(surface, "HIGH SCORES", FONT_MED, CYAN, (WIDTH / 2, 90), glow=BLUE)
        y = 170
        if not self.highscores_cache:
            draw_text_center(surface, "No scores yet - go fly!", FONT_SMALL, GRAY, (WIDTH / 2, y))
        for i, entry in enumerate(self.highscores_cache[:10]):
            if entry.get("endless"):
                loop_num = max(1, entry["stage"] - NUM_STAGES + 1)
                stage_label = f"Endless (loop {loop_num})"
            else:
                stage_label = f"Stage {entry['stage']}"
            draw_text_center(surface, f"{i + 1}.  {entry['score']}  -  {stage_label}", FONT_SMALL, WHITE, (WIDTH / 2, y))
            y += 42
        for action, btn in self.build_highscores_buttons():
            btn.draw(surface)

    def build_stage_select_buttons(self):
        buttons = []
        cols, rows = 2, 5
        margin = 60
        gap = 18
        w = (WIDTH - margin * 2 - gap * (cols - 1)) / cols
        h = 78
        top = 160
        for i in range(NUM_STAGES):
            col = i % cols
            row = i // cols
            x = margin + col * (w + gap)
            y = top + row * (h + gap)
            buttons.append((f"stage_{i + 1}", Button((x, y, w, h), f"STAGE {i + 1}")))
        buttons.append(("back", Button((WIDTH / 2 - 100, top + rows * (h + gap) + 20, 200, 54), "BACK")))
        return buttons

    def draw_stage_select(self, surface):
        draw_text_center(surface, "STAGE SELECT", FONT_MED, CYAN, (WIDTH / 2, 90), glow=BLUE)
        for action, btn in self.build_stage_select_buttons():
            btn.draw(surface)

    def build_pause_buttons(self):
        w, h, gap = WIDTH - 200, 54, 14
        x = 100
        y = 320
        buttons = [("resume", Button((x, y, w, h), "RESUME"))]
        y += h + gap
        buttons.append(("restart_stage", Button((x, y, w, h), "RESTART STAGE")))
        y += h + gap
        half = (w - gap) / 2
        buttons.append(("fullscreen", Button((x, y, half, h), "FULLSCREEN: " + ("ON" if CONFIG.get("fullscreen") else "OFF"))))
        buttons.append(("bgm", Button((x + half + gap, y, half, h), "MUSIC: " + ("ON" if BGM_ON else "OFF"))))
        y += h + gap
        buttons.append(("sfx", Button((x, y, half, h), "SFX: " + ("ON" if SFX_ON else "OFF"))))
        buttons.append(("main_menu", Button((x + half + gap, y, half, h), "MAIN MENU")))
        return buttons

    # ---------------- input ----------------
    def handle_keydown(self, key):
        if key == pygame.K_ESCAPE:
            if self.state == STATE_MENU:
                self.quit_game()
            elif self.state in (STATE_HIGHSCORES, STATE_STAGE_SELECT):
                self.state = STATE_MENU
                play_sound("ui_click")
            elif self.state in (STATE_PLAY, STATE_READY, STATE_BOSS_INTRO, STATE_PAUSED):
                self.toggle_pause()
            elif self.state in (STATE_GAME_OVER, STATE_VICTORY, STATE_STAGE_CLEAR):
                self.finalize_score()
                self.state = STATE_MENU
                set_cursor_locked(False)
                play_sound("ui_click")
            return
        if key == pygame.K_p and self.state in (STATE_PLAY, STATE_PAUSED, STATE_READY, STATE_BOSS_INTRO):
            self.toggle_pause()
        if key == pygame.K_r and self.state in (STATE_GAME_OVER, STATE_VICTORY):
            log("Restart requested.")
            if self.endless:
                self.start_endless()
            else:
                self.reset_full()
        if key == pygame.K_SPACE and self.state == STATE_STAGE_CLEAR:
            play_sound("ui_click")
            self.next_stage()
        if key in (pygame.K_LSHIFT, pygame.K_RSHIFT) and self.state == STATE_PLAY:
            self._missile_tap_pending = True
        if key == pygame.K_m:
            quick_toggle_mute()
        if key in (pygame.K_LCTRL, pygame.K_RCTRL, pygame.K_LALT, pygame.K_RALT):
            if self.state in (STATE_PLAY, STATE_READY, STATE_BOSS_INTRO, STATE_PAUSED):
                currently_visible = pygame.mouse.get_visible()
                set_cursor_locked(currently_visible)
                log(f"Cursor toggled: now {'hidden+locked' if currently_visible else 'shown+free'}")

    def handle_mouse_down(self, button, pos):
        if button == 3 and self.state == STATE_PLAY:
            self._missile_tap_pending = True
            return
        if button != 1:
            return

        if self.state == STATE_MENU:
            for action, btn in self.build_menu_buttons():
                if btn.hit(pos):
                    self.handle_menu_action(action)
                    return
            return

        if self.state == STATE_HIGHSCORES:
            for action, btn in self.build_highscores_buttons():
                if btn.hit(pos):
                    self.state = STATE_MENU
                    play_sound("ui_click")
                    return
            return

        if self.state == STATE_STAGE_SELECT:
            for action, btn in self.build_stage_select_buttons():
                if btn.hit(pos):
                    if action == "back":
                        self.state = STATE_MENU
                    elif action.startswith("stage_"):
                        self.start_at_stage(int(action.split("_")[1]))
                    play_sound("ui_click")
                    return
            return

        if self.state == STATE_PAUSED:
            for action, btn in self.build_pause_buttons():
                if btn.hit(pos):
                    if action == "resume":
                        self.toggle_pause()
                    elif action == "main_menu":
                        self.finalize_score()
                        self.state = STATE_MENU
                        set_cursor_locked(False)
                        play_sound("ui_click")
                    elif action == "restart_stage":
                        self.restart_stage()
                        play_sound("ui_click")
                    else:
                        self.handle_menu_action(action)
                    return
            return

        if self.state in (STATE_READY, STATE_PLAY, STATE_BOSS_INTRO) and PAUSE_ICON_RECT.collidepoint(pos):
            self.toggle_pause()
            return

        if self.state == STATE_STAGE_CLEAR:
            play_sound("ui_click")
            self.next_stage()
            return

        if self.state == STATE_GAME_OVER or self.state == STATE_VICTORY:
            return

    # ---------------- main loop ----------------
    def run(self):
        global window
        mouse_pos = (WIDTH / 2, HEIGHT / 2)
        last_mouse_pos = mouse_pos
        while True:
            dt = clock.tick(FPS) / 1000.0
            dt = min(dt, 0.05)  # clamp huge dt spikes (e.g. window drag) to avoid physics blowups
            try:
                win_w, win_h = window.get_size()
                raw_mouse = pygame.mouse.get_pos()
                if win_w > 0 and win_h > 0:
                    mouse_pos = window_to_logical(raw_mouse)
                mouse_moved = mouse_pos != last_mouse_pos
                last_mouse_pos = mouse_pos

                now = pygame.time.get_ticks() / 1000.0
                for event in pygame.event.get():
                    if event.type == pygame.QUIT:
                        log("Window close event detected, quitting.")
                        self.quit_game()
                    elif event.type == pygame.KEYDOWN:
                        self.handle_keydown(event.key)
                    elif event.type == pygame.MOUSEBUTTONDOWN:
                        self.handle_mouse_down(event.button, mouse_pos)
                    elif event.type == pygame.VIDEORESIZE:
                        if not CONFIG.get("fullscreen", False) and now >= _suppress_resize_until:
                            new_w = max(MIN_WINDOW_W, event.w)
                            new_h = max(MIN_WINDOW_H, event.h)
                            window = pygame.display.set_mode((new_w, new_h), pygame.RESIZABLE)
                            CONFIG["window_w"], CONFIG["window_h"] = new_w, new_h
                            self._geometry_dirty = True
                    elif hasattr(pygame, "WINDOWMOVED") and event.type == pygame.WINDOWMOVED:
                        if not CONFIG.get("fullscreen", False) and now >= _suppress_resize_until:
                            CONFIG["window_x"], CONFIG["window_y"] = event.x, event.y
                            self._geometry_dirty = True

                if self._geometry_dirty:
                    self._geometry_save_accum += dt
                    if self._geometry_save_accum > 1.5:
                        save_config(CONFIG)
                        self._geometry_dirty = False
                        self._geometry_save_accum = 0.0

                keys = pygame.key.get_pressed()
                self.update(dt, keys, mouse_pos, mouse_moved)
                self.draw()

                shake_offset = (0, 0)
                if self.shake_time_left > 0:
                    frac = self.shake_time_left / max(0.001, self.shake_duration_total)
                    amt = self.shake_magnitude * frac
                    shake_offset = (random.randint(-int(amt), int(amt)), random.randint(-int(amt), int(amt)))
                present(shake_offset)
            except SystemExit:
                raise
            except Exception:
                import traceback

                log("Unexpected error in main loop:\n" + traceback.format_exc())
                if window is None:
                    try:
                        window = pygame.display.set_mode((480, 854), pygame.RESIZABLE)
                    except Exception:
                        pass


if __name__ == "__main__":
    log("--- VOID BREAKER STARTED ---")
    Game().run()