"""Cryptwave: Digital Exorcism — Display & Input

Terminal I/O, keyboard polling, double-buffered ANSI rendering.
"""

import sys
import os
import math
import random
from typing import Tuple, Optional, List

# UTF-8 output for Unicode symbols on Windows
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from engine import (
    Game, Player, GroundMaw, AirThreat, Archway, DeathPhase,
    DeathParticle, DeathRose, Actions,
    COLS, ROWS, PLAY_TOP, PLAY_BOTTOM, GROUND_ROW, PLAYER_X,
    DASH_DURATION, DASH_COOLDOWN,
    MAW_TOP_ROW,
)

# platform input
if sys.platform == 'win32':
    import msvcrt
    import ctypes
else:
    import tty
    import termios
    import select
    _old_settings = None


# ============================================================
# TERMINAL SETUP
# ============================================================

def setup_terminal():
    """Raw mode, ANSI enabled, cursor hidden."""
    if sys.platform == 'win32':
        STD_OUTPUT_HANDLE = -11
        ENABLE_VT = 0x0004
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(STD_OUTPUT_HANDLE)
        mode = ctypes.c_uint32()
        kernel32.GetConsoleMode(handle, ctypes.byref(mode))
        kernel32.SetConsoleMode(handle, mode.value | ENABLE_VT)
    else:
        global _old_settings
        _old_settings = termios.tcgetattr(sys.stdin)
        tty.setraw(sys.stdin)

    sys.stdout.write('\033[?25l\033[2J\033[H')
    sys.stdout.flush()


def restore_terminal():
    """Show cursor, restore mode, clear screen."""
    sys.stdout.write('\033[?25h\033[2J\033[H')
    sys.stdout.flush()
    if sys.platform != 'win32' and _old_settings is not None:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, _old_settings)


def get_terminal_size() -> Tuple[int, int]:
    try:
        sz = os.get_terminal_size()
        return sz.columns, sz.lines
    except (ValueError, OSError):
        return COLS, ROWS


# ============================================================
# KEYBOARD INPUT
# ============================================================

def poll_input() -> Optional[str]:
    """Non-blocking. Returns 'jump','duck','dash','quit', or None."""
    if sys.platform == 'win32':
        return _poll_windows()
    else:
        return _poll_unix()


def _poll_windows() -> Optional[str]:
    if not msvcrt.kbhit():
        return None
    ch = msvcrt.getch()
    if ch == b' ':
        return 'jump'
    if ch == b'\x1b':
        return 'quit'
    if ch in (b'w', b'W'):
        return 'jump'
    if ch in (b's', b'S'):
        return 'duck'
    if ch in (b'd', b'D'):
        return 'dash'
    if ch in (b'\xe0', b'\x00'):
        ch2 = msvcrt.getch()
        if ch2 == b'H':   return 'jump'
        if ch2 == b'P':   return 'duck'
        if ch2 == b'M':   return 'dash'
    return None


def _poll_unix() -> Optional[str]:
    if not select.select([sys.stdin], [], [], 0)[0]:
        return None
    ch = sys.stdin.read(1)
    if ch == ' ':
        return 'jump'
    if ch == '\x1b':
        if select.select([sys.stdin], [], [], 0.01)[0]:
            ch2 = sys.stdin.read(1)
            if ch2 == '[':
                ch3 = sys.stdin.read(1)
                if ch3 == 'A':   return 'jump'
                if ch3 == 'B':   return 'duck'
                if ch3 == 'C':   return 'dash'
        return 'quit'
    if ch in ('w', 'W'):   return 'jump'
    if ch in ('s', 'S'):   return 'duck'
    if ch in ('d', 'D'):   return 'dash'
    if ch == '\x03':       return 'quit'
    return None


def actions_from_input() -> Actions:
    """Drain input queue into an Actions struct."""
    act = Actions()
    while True:
        key = poll_input()
        if key is None:
            break
        if key == 'jump':   act.jump = True
        elif key == 'duck': act.duck = True
        elif key == 'dash': act.dash = True
        elif key == 'quit': act.quit = True
    return act


# ============================================================
# SCREEN BUFFER
# ============================================================

class ScreenBuffer:
    """Flicker-free double buffer. Build frame, flush once."""

    def __init__(self):
        self.chars = [[' ' for _ in range(COLS)] for _ in range(ROWS)]
        self.colors = [['' for _ in range(COLS)] for _ in range(ROWS)]
        self.z = [[0 for _ in range(COLS)] for _ in range(ROWS)]

    def clear(self, base_z: int = 0):
        for y in range(ROWS):
            rc = self.chars[y]
            rclr = self.colors[y]
            rz = self.z[y]
            for x in range(COLS):
                rc[x] = ' '
                rclr[x] = ''
                rz[x] = base_z

    def put(self, x: int, y: int, char: str, color: str = '', z: int = 0):
        if 0 <= x < COLS and 0 <= y < ROWS:
            if z >= self.z[y][x]:
                self.z[y][x] = z
                self.chars[y][x] = char
                self.colors[y][x] = color

    def put_str(self, x: int, y: int, s: str, color: str = '', z: int = 0):
        for i, ch in enumerate(s):
            self.put(x + i, y, ch, color, z)

    def flush(self):
        out = ['\033[H']
        for y in range(ROWS):
            rc = self.chars[y]
            rclr = self.colors[y]
            for x in range(COLS):
                c = rclr[x]
                if c:
                    out.append(c)
                out.append(rc[x])
            if y < ROWS - 1:
                out.append('\r\n')
        out.append('\033[0m')
        sys.stdout.write(''.join(out))
        sys.stdout.flush()


# ============================================================
# ANSI COLOR SHORTCUTS
# ============================================================

C_RESET   = '\033[0m'
C_CYAN    = '\033[96m'
C_MAGENTA = '\033[95m'
C_WHITE   = '\033[97m'
C_RED     = '\033[91m'
C_YELLOW  = '\033[93m'
C_PURPLE  = '\033[35m'
C_DIM     = '\033[2m'
C_DIM_W   = '\033[37m'           # dim white for ground
C_DARK_P  = '\033[38;5;53m'      # dark purple (moon)
C_CASTLE  = '\033[38;5;54m'      # deeper purple (death-screen castle backdrop)

# Castle on a Precipice — Stephen Wilson (asciiart.eu)
# Rendered as a dim background during the death sequence.
CASTLE_ART = [
    "       .         .      /\\      .:  *       .          .              .",
    "                 *    .'  `.      .     .     *      .                  .",
    "  :             .    /      \\  _ .________________  .                    .",
    "       |            `.+-~~-+.'/.' `.^^^^^^^^\\~~~~~\\.                      .",
    " .    -*-   . .       |u--.|  /     \\~~~~~~~|~~~~~|",
    "       |              |   u|.'       `.\" \"  |\" \" \"|                        .",
    "    :            .    |.u-./ _..---.._ \\\" \" | \" \" |",
    "   -*-            *   |    ~-|U U U U|-~____L_____L_                      .",
    "    :         .   .   |.-u.| |..---..|\"//// ////// /\\       .            .",
    "          .  *        |u   | |       |// /// // ///==\\     / \\          .",
    " .          :         |.--u| |..---..|//////~\\////====\\   /   \\       .",
    "      .               | u  | |       |~~~~/\\u |~~|++++| .`+~~~+'  .",
    "                      |.-|~U~U~|---..|u u|u | |u ||||||   |  U|",
    "                   /~~~~/-\\---.'     |===|  |u|==|++++|   |   |",
    "          aaa      |===| _ | ||.---..|u u|u | |u ||HH||U~U~U~U~|        aa@@",
    "     aaa@@@@@@aa   |===|||||_||      |===|_.|u|_.|+HH+|_/_/_/_/aa    a@@@@@@",
    " aa@@@@@@@@@@@@@@a |~~|~~~~\\---/~-.._|--.---------.~~~`.__ _.@@@@@@a    ~~~~",
    "   ~~~~~~    ~~~    \\_\\\\ \\  \\/~ //\\  ~,~|  __   | |`.   :||  ~~~~",
    "                     a\\`| `   _//  | / _| || |  | `.'  ,''|     aa@@@@@@@a",
    " aaa   aaaa       a@@@@\\| \\  //'   |  // \\`| |  `.'  .' | |  aa@@@@@@@@@@@@@",
    "@@@@@a@@@@@@a      ~~~~~ \\\\`//| | \\ \\//   \\`  .-'  .' | '/      ~~~~~~~  ~~",
    "@S.C.E.S.W.@@@@a          \\// |.`  ` ' /~  :-'   .'|  '/~aa",
    "~~~~~~ ~~~~~~         a@@@|   \\\\ |   // .'    .'| |  |@@@@@@a",
    "                    a@@@@@@@\\   | `| ''.'     .' | ' /@@@@@@@@@a",
]


# ============================================================
# RENDERER
# ============================================================

class Renderer:
    """Draws the full game world each frame."""

    def __init__(self):
        self.buf = ScreenBuffer()
        self._frame = 0
        self._dash_trail: List[Tuple[int, int, int]] = []  # (x, y, remaining_frames)

    def render(self, game: Game):
        self._frame += 1
        self.buf.clear()

        if game.playing:
            self._draw_playfield(game)
            self._draw_moon()
            self._draw_obstacles(game)
            self._draw_player(game)
        else:
            self._draw_death(game)

        self._draw_hud(game)
        self.buf.flush()

    # ================================================================
    # PLAYFIELD
    # ================================================================

    def _draw_playfield(self, game: Game):
        # Ground line: row 22, "=" across all 80 cols
        # Cyan pulse every 30 frames
        pulse = (self._frame % 30 == 0)
        ground_color = C_CYAN if pulse else C_DIM_W
        for x in range(COLS):
            self.buf.put(x, GROUND_ROW, '=', ground_color, z=5)

    def _draw_moon(self):
        """Glitching moon — flickers between dark purple, magenta, and white."""
        mx, my = COLS - 12, 2   # top-right
        moon_art = [
    "  _______  ",
    " /  \\ /  \\ ",
    "|  █   █  |",
    "|         |",
    "| x▄x▄x▄x |",
    " \\_______/ ",
]
        for dy, line in enumerate(moon_art):
            for dx, ch in enumerate(line):
                if ch != ' ':
                    clr = random.choice([C_DARK_P, C_PURPLE, C_MAGENTA, C_WHITE])
                    self.buf.put(mx + dx, my + dy, ch, clr, z=8)

    # ================================================================
    # OBSTACLES
    # ================================================================

    def _draw_obstacles(self, game: Game):
        wo = game.world_offset
        for obs in game.obstacles:
            sx = int(round(obs.world_x - wo))
            if sx < -2 or sx >= COLS + 2:
                continue
            if isinstance(obs, GroundMaw):
                self._draw_maw(obs, sx)
            elif isinstance(obs, AirThreat):
                self._draw_air(obs, sx)
            elif isinstance(obs, Archway):
                self._draw_arch(obs, sx)

    def _draw_maw(self, maw: GroundMaw, sx: int):
        # Always mark the ground tile so the spike location is visible
        clr = C_DIM if not maw.dangerous else maw.color
        self.buf.put(sx, GROUND_ROW, maw.symbol, clr, z=10)
        # Draw on upper rows when emerged
        if maw.dangerous:
            for row in maw.rows_occupied():
                if row != GROUND_ROW:
                    self.buf.put(sx, row, maw.symbol, maw.color, z=10)

    def _draw_air(self, air: AirThreat, sx: int):
        y = air.tile_y
        # Yellow static trail behind
        if sx > 0:
            self.buf.put(sx - 1, y, '▒', C_YELLOW, z=8)
            if sx > 1:
                self.buf.put(sx - 2, y,
                             random.choice(['·', '`', ' ']), C_YELLOW, z=7)
        self.buf.put(sx, y, air.symbol, air.color, z=10)

    def _draw_arch(self, arch: Archway, sx: int):
        for y in range(PLAY_TOP, GROUND_ROW + 1):
            if arch.in_gap(y):
                continue
            # Glitch fragments (12% chance per tile)
            if random.random() < 0.12:
                ch = random.choice(['#', '▓', '▒', '░', ' '])
                clr = C_WHITE
            else:
                if y == arch.gap_top - 1 or y == arch.gap_bottom:
                    ch = '#'
                elif y < arch.gap_top:
                    ch = '▓'
                else:
                    ch = '▒'
                clr = C_PURPLE
            self.buf.put(sx, y, ch, clr, z=10)

    # ================================================================
    # PLAYER
    # ================================================================

    def _draw_player(self, game: Game):
        p = game.player
        px = PLAYER_X
        py = p.tile_y

        # After-image trail
        if p.dashing:
            self._dash_trail.append((px, py, 6))
        new_trail = []
        for tx, ty, life in self._dash_trail:
            if life > 0:
                new_trail.append((tx, ty, life - 1))
                if life > 1:
                    self.buf.put(tx, ty, '▒',
                                 f'\033[38;5;{232 + life * 3}m', z=9)
        self._dash_trail = new_trail

        # Color — flash white during duck-cancel slam
        clr = p.color
        if not p.grounded and p.vy > 1.5:
            clr = C_WHITE

        # Single 1×1 tile: ▶ / ▀ / 🕆
        self.buf.put(px, py, p.symbol, clr, z=15)

        # Cooldown bar above player
        cd = p.cooldown_fraction
        if cd > 0:
            bar_y = py - 1
            if bar_y >= 0:
                bar_w = 6
                filled = int(bar_w * (1.0 - cd))
                bar_str = '[' + '=' * filled + ' ' * (bar_w - filled) + ']'
                self.buf.put_str(px - 1, bar_y, bar_str, C_YELLOW, z=14)

    # ================================================================
    # HUD
    # ================================================================

    def _draw_hud(self, game: Game):
        # SCORE: 0000 (leading zeros, 4+ digits)
        score_str = f"SCORE: {game.score:04d}"
        self.buf.put_str(0, 0, score_str, C_YELLOW, z=20)

        # Dash status
        if game.playing and game.player.dash_cooldown <= 0:
            ready = '[DASH READY]'
            self.buf.put_str(COLS - len(ready) - 1, 0, ready, C_CYAN, z=20)

    # ================================================================
    # DEATH SCREEN
    # ================================================================

    def _draw_death(self, game: Game):
        g = game

        if g.phase == DeathPhase.FREEZE:
            self._draw_playfield(game)
            self._draw_moon()
            self._draw_obstacles(game)
            # Explosion particles
            for p in g.death_particles:
                if p.life > 0:
                    alpha = p.life / 5.0
                    if alpha > 0.1:
                        self.buf.put(int(p.x), int(p.y), p.symbol, p.color, z=15)
            return

        if g.phase == DeathPhase.FADE:
            self._draw_playfield(game)
            self._draw_moon()
            self._draw_obstacles(game)
            for p in g.death_particles:
                if p.life > 0:
                    self.buf.put(int(p.x), int(p.y), p.symbol, p.color, z=15)
            # Fade: progressively blank the screen
            fade_frac = g.death_frame / 10.0       # 0 → 1
            self._overlay_fade(fade_frac)
            return

        # FORMATION / EPITAPH / WAIT — black screen
        if g.phase in (DeathPhase.FORMATION, DeathPhase.EPITAPH, DeathPhase.WAIT):
            # Castle backdrop (z=15: above roses, below epitaph & HUD).
            # Pad all lines to the same width, then center as a single block.
            _castle_w = max(len(ln) for ln in CASTLE_ART)
            _castle_x = max(0, (COLS - _castle_w) // 2)
            for dy, line in enumerate(CASTLE_ART):
                padded = line.ljust(_castle_w)
                for dx, ch in enumerate(padded):
                    if ch != ' ':
                        self.buf.put(_castle_x + dx, dy, ch, C_CASTLE, z=15)

        # Roses & symbols (materialize during FORMATION, persist through EPITAPH & WAIT)
        if g.phase in (DeathPhase.FORMATION, DeathPhase.EPITAPH, DeathPhase.WAIT):
            for rose in g.death_roses:
                if g.phase == DeathPhase.FORMATION:
                    visible = g.death_frame >= rose.spawn_frame
                else:
                    visible = True
                if visible:
                    # Flickering colors
                    clr = random.choice([C_RED, C_MAGENTA, C_WHITE])
                    self.buf.put_str(rose.x, rose.y, rose.symbol, clr, z=10)

        # Epitaph — below castle's main body so the art stays intact
        if g.phase in (DeathPhase.EPITAPH, DeathPhase.WAIT):
            msg = g.death_epitaph
            cx = max(0, (COLS - len(msg)) // 2)
            cy = 18                      # below the castle's upper detail
            # Flicker every 3 frames
            if g.death_frame % 6 < 4:
                self.buf.put_str(cx, cy, msg, C_RED, z=20)
            else:
                self.buf.put_str(cx, cy, msg, C_WHITE, z=20)

            # Hint
            hint = "[ PRESS SPACE TO RECOMPILE ]"
            hx = max(0, (COLS - len(hint)) // 2)
            self.buf.put_str(hx, cy + 2, hint, C_DIM, z=20)

    def _overlay_fade(self, frac: float):
        """frac: 0=normal, 1=fully blacked out."""
        if frac <= 0:
            return
        for y in range(ROWS):
            for x in range(COLS):
                if self.buf.chars[y][x] != ' ' and random.random() < frac:
                    self.buf.put(x, y, ' ', z=99)
