"""Cryptwave: Digital Exorcism — Game Engine

Fixed 80×24 terminal grid. Entity physics, Sentence-based map generation,
collision detection, and the death-sequence state machine.

Coordinate system (0-based, spec uses 1-based):
  Row  0  = spec row  1  — HUD, play-area ceiling
  Row 21  = spec row 22  — player stands here
  Row 22  = spec row 23  — ground line ("====")
"""

import random
import math
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# ============================================================
# GRID CONSTANTS  (spec §Terminal Layout)
# ============================================================

COLS = 80
ROWS = 24

HUD_ROW = 0                # spec row 1
PLAY_TOP = 0               # spec row 1  (top of playable area)
PLAY_BOTTOM = 21           # spec row 22 (player's feet row, last playable row)
GROUND_ROW = 22            # spec row 23 (visual ground line "====")
PLAYER_X = 14              # spec col 14, fixed screen column

# ============================================================
# PHYSICS CONSTANTS  (spec §Player Physics)
# ============================================================

FPS = 30
GRAVITY = 0.3              # tiles / frame²
JUMP_VELOCITY = -2.5       # tiles / frame (upward)
JUMP_HORIZONTAL = 0.5      # extra tiles / frame forward while airborne
FAST_GRAVITY = 10          # duck-cancel gravity (doubled while rising)
TERMINAL_VELOCITY = 5.0    # max downward speed

# Dash  (spec §The Dash Mechanic)
DASH_DURATION = 5          # frames (5 = 0.15 s)
DASH_COOLDOWN = 30         # frames (15 = 0.5 s)
DASH_TILES = 5             # instant teleport distance

# World scroll — CONSTANT per spec
SCROLL_SPEED = 10.0        # tiles / second
SCROLL_PER_TICK = SCROLL_SPEED / FPS

# Ground Maw lifecycle  (spec §The Ground Maw)
MAW_RISE_FRAMES = 18       # 0.6 s
MAW_HOLD_FRAMES = 6        # 0.2 s
MAW_SINK_FRAMES = 18       # 0.6 s
MAW_TOTAL_FRAMES = MAW_RISE_FRAMES + MAW_HOLD_FRAMES + MAW_SINK_FRAMES
MAW_TOP_ROW = 20           # spec row 21 — top of fully-emerged maw (0-based)
MAW_BOTTOM_ROW = GROUND_ROW

# Air Threat  (spec §The Air Threat)
AIR_SPAWN_MIN = 18         # spec row 19, 0-based (head-height upper)
AIR_SPAWN_MAX = 20         # spec row 21, 0-based
AIR_BOB_AMPLITUDE = 3      # ±3 rows → hits player at row 21 when bobbed down
AIR_BOB_SPEED = (2 * math.pi) / (FPS * 2.0)  # period = 2 seconds

# Archway  (spec §The Glitching Archway)
ARCH_GAP_HEIGHT = 2        # rows
ARCH_OSC_BASE = 0.03       # rad / frame at score 0
ARCH_OSC_MAX = 0.12        # rad / frame at score 500+

# Map generation  (spec §Map Generation)
SENTENCE_GAP_EARLY = 10    # tiles between obstacles (early)
SENTENCE_GAP_LATE = 4      # tiles between obstacles (late)
SPAWN_LOOKAHEAD = 50       # world-units ahead to generate

# ============================================================
# ACTIONS
# ============================================================

@dataclass
class Actions:
    jump: bool = False
    duck: bool = False
    dash: bool = False
    quit: bool = False


# ============================================================
# PLAYER
# ============================================================

class Player:
    """1×1 exorcist at fixed screen X=14. Y varies with jump/gravity."""

    def __init__(self):
        self.y: float = float(PLAY_BOTTOM)   # start on ground (row 21)
        self.vy: float = 0.0
        self.grounded: bool = True
        self.ducking: bool = False
        self.dashing: bool = False
        self.dash_timer: int = 0
        self.dash_cooldown: int = 0
        self.alive: bool = True
        # Visual pulse for player symbol
        self._pulse: int = 0

    # -- properties --

    @property
    def symbol(self) -> str:
        """▶ when dashing, ▀ when ducking, 🕆 normally."""
        if self.dashing:
            return '🕀'
        if self.ducking:
            return '🕁'  
        return '🕂'

    @property
    def color(self) -> str:
        if self.dashing:
            return '\033[97m'             # bright white
        return '\033[96m'                 # cyan

    @property
    def immune(self) -> bool:
        return self.dashing

    @property
    def tile_y(self) -> int:
        return int(round(self.y))

    @property
    def cooldown_fraction(self) -> float:
        if self.dash_cooldown <= 0:
            return 0.0
        return self.dash_cooldown / DASH_COOLDOWN

    def _clamp(self):
        """Enforce terminal velocity and ground collision."""
        if self.vy > TERMINAL_VELOCITY:
            self.vy = TERMINAL_VELOCITY
        if self.y >= PLAY_BOTTOM:
            self.y = float(PLAY_BOTTOM)
            self.vy = 0.0
            self.grounded = True
        if self.y < PLAY_TOP:
            self.y = float(PLAY_TOP)
            self.vy = max(0.0, self.vy)

    # -- inputs --

    def jump(self):
        if self.grounded:
            self.vy = JUMP_VELOCITY
            self.grounded = False

    def try_dash(self) -> bool:
        if self.dash_cooldown <= 0 and not self.dashing:
            self.dashing = True
            self.dash_timer = DASH_DURATION
            self.dash_cooldown = DASH_COOLDOWN
            return True
        return False

    def update(self, actions: Actions):
        self._pulse += 1

        # cooldown tick
        if self.dash_cooldown > 0:
            self.dash_cooldown -= 1

        # dash timer
        if self.dashing:
            self.dash_timer -= 1
            if self.dash_timer <= 0:
                self.dashing = False

        # ducking state
        self.ducking = actions.duck

        # inputs
        if actions.jump:
            self.jump()
        if actions.dash:
            self.try_dash()

        # physics
        if not self.grounded:
            grav = FAST_GRAVITY if (actions.duck and self.vy < 0) else GRAVITY
            self.vy += grav
            self.y += self.vy
            self._clamp()


# ============================================================
# OBSTACLES
# ============================================================

class GroundMaw:
    """3-row-tall ground obstacle (rows 20-22 when fully emerged).
    Rise: 18 frames → Hold: 6 frames → Sink: 18 frames."""

    def __init__(self, world_x: float):
        self.world_x = world_x
        self.age = 0

    @property
    def symbol(self) -> str:
        return '†'

    @property
    def color(self) -> str:
        return '\033[95m' if (self.age // 3) % 2 == 0 else '\033[97m'

    @property
    def alive(self) -> bool:
        return True          # never dies — cycles perpetually

    @property
    def top_row(self) -> int:
        """The highest row the maw occupies right now (0-based)."""
        if self.age < MAW_RISE_FRAMES:
            frac = self.age / MAW_RISE_FRAMES
            return GROUND_ROW - int(frac * 2)       # rises 2 rows (23→21 spec)
        elif self.age < MAW_RISE_FRAMES + MAW_HOLD_FRAMES:
            return MAW_TOP_ROW                       # fully emerged at row 20
        elif self.age < MAW_TOTAL_FRAMES:
            sink_age = self.age - MAW_RISE_FRAMES - MAW_HOLD_FRAMES
            frac = 1.0 - (sink_age / MAW_SINK_FRAMES)
            return GROUND_ROW - int(frac * 2)
        return GROUND_ROW

    @property
    def dangerous(self) -> bool:
        """Dangerous once risen above ground level."""
        return self.top_row < GROUND_ROW

    def rows_occupied(self) -> range:
        """Rows this maw currently occupies (0-based)."""
        return range(self.top_row, GROUND_ROW + 1)

    def update(self):
        self.age = (self.age + 1) % MAW_TOTAL_FRAMES

    def screen_x(self, world_offset: float) -> int:
        return int(round(self.world_x - world_offset))

    def collides_with(self, px: int, py: int, world_offset: float) -> bool:
        if not self.dangerous:
            return False
        return (self.screen_x(world_offset) == px and
                py in self.rows_occupied())


class AirThreat:
    """Mid-air obstacle at head-height (spec rows 16-18).
    Bobs ±3 rows vertically (2-second period), reaching the player on ground."""

    SYMBOLS = ['♩', '♪', '♫', '۞']

    def __init__(self, world_x: float):
        self.world_x = world_x
        self.center_y = random.randint(AIR_SPAWN_MIN, AIR_SPAWN_MAX)
        self.symbol = random.choice(self.SYMBOLS)
        self.phase = random.uniform(0, math.pi * 2)

    @property
    def color(self) -> str:
        return '\033[91m'                      # hot pink

    @property
    def tile_y(self) -> int:
        bob = int(round(math.sin(self.phase) * AIR_BOB_AMPLITUDE))
        y = self.center_y + bob
        return max(PLAY_TOP, min(PLAY_BOTTOM, y))

    def update(self):
        self.phase += AIR_BOB_SPEED

    def screen_x(self, world_offset: float) -> int:
        return int(round(self.world_x - world_offset))

    def collides_with(self, px: int, py: int, world_offset: float) -> bool:
        return self.screen_x(world_offset) == px and py == self.tile_y


class Archway:
    """Full-wall obstacle from row 0 to row 22 (spec rows 1-23).
    Has a 2-row oscillating gap. Player must dash through or thread the gap."""

    def __init__(self, world_x: float, oscillation_speed: float):
        self.world_x = world_x
        self.gap_center = PLAY_BOTTOM // 2       # middle of play area
        self.osc_phase = random.uniform(0, math.pi * 2)
        self.osc_speed = oscillation_speed

    @property
    def gap_top(self) -> int:
        half_range = (PLAY_BOTTOM - PLAY_TOP - ARCH_GAP_HEIGHT) // 2
        offset = int(round(math.sin(self.osc_phase) * half_range))
        return self.gap_center + offset

    @property
    def gap_bottom(self) -> int:
        return self.gap_top + ARCH_GAP_HEIGHT

    def update(self):
        self.osc_phase += self.osc_speed

    def screen_x(self, world_offset: float) -> int:
        return int(round(self.world_x - world_offset))

    def in_gap(self, y: int) -> bool:
        return self.gap_top <= y < self.gap_bottom

    def collides_with(self, px: int, py: int, world_offset: float) -> bool:
        if self.screen_x(world_offset) != px:
            return False
        return not self.in_gap(py)


# ============================================================
# MAP GENERATION
# ============================================================

class SentenceKind(Enum):
    STUTTER = auto()   # Air → Air → Ground          (duck, duck, jump)
    WHIP    = auto()   # Ground → Air                (jump, duck-cancel)
    GAMBLE  = auto()   # Archway → Ground            (thread + jump, or dash)
    CHORD   = auto()   # 3 Air stacked vertically    (timed duck)
    CROSS   = auto()   # Ground → Archway → Ground   (jump, dash, jump)

@dataclass
class ObstacleSpawn:
    world_x: float
    kind: str            # 'maw' | 'air' | 'arch'
    center_y: Optional[int] = None    # air threats: specific height override

class MapGenerator:
    """Sentence-based obstacle spawning with difficulty scaling.

    All five sentences available from the start.
    Difficulty only affects pacing: gap size (10→4 tiles) and
    archway oscillation speed (0.03→0.12 rad/frame).
    """

    def __init__(self):
        self.next_x: float = 60.0
        self.difficulty: float = 0.0
        self._pending: List[ObstacleSpawn] = []

    # -- helpers --

    def _gap(self) -> int:
        """Inter-obstacle gap (10 → 4 tiles)."""
        base = 10 - int(self.difficulty * 6)
        return max(4, base + random.randint(0, 2))

    def _sentence_gap(self) -> int:
        """Gap after a sentence (shrinks with difficulty)."""
        return self._gap() + random.randint(5, 10)

    @property
    def osc_speed(self) -> float:
        """Archway oscillation (0.03 → 0.12)."""
        return ARCH_OSC_BASE + self.difficulty * (ARCH_OSC_MAX - ARCH_OSC_BASE)

    # -- sentence generators --

    def _gen_stutter(self):
        """Air → Air → Ground  (duck, duck, jump)."""
        g = self._gap()
        self._pending.append(ObstacleSpawn(self.next_x, 'air'))
        self._pending.append(ObstacleSpawn(self.next_x + g, 'air'))
        self._pending.append(ObstacleSpawn(self.next_x + g * 2, 'maw'))
        self.next_x += g * 2 + self._sentence_gap()

    def _gen_whip(self):
        """Ground → Air  (jump → duck-cancel)."""
        g = self._gap()
        self._pending.append(ObstacleSpawn(self.next_x, 'maw'))
        self._pending.append(ObstacleSpawn(self.next_x + g, 'air'))
        self.next_x += g + self._sentence_gap()

    def _gen_gamble(self):
        """Archway → Ground  (thread + jump or dash)."""
        g = self._gap()
        self._pending.append(ObstacleSpawn(self.next_x, 'arch'))
        self._pending.append(ObstacleSpawn(self.next_x + g, 'maw'))
        self.next_x += g + self._sentence_gap()

    def _gen_chord(self):
        """3 Air Threats at different heights, all bobbing out of phase."""
        heights = [
            PLAY_BOTTOM - 4,   # near head
            PLAY_BOTTOM - 6,   # mid
            PLAY_BOTTOM - 8,   # high
        ]
        random.shuffle(heights)
        for i, cy in enumerate(heights[:3]):
            self._pending.append(ObstacleSpawn(self.next_x + i * 2, 'air', cy))
        self.next_x += 4 + self._sentence_gap()

    def _gen_cross(self):
        """Ground → Archway → Ground  (jump, dash, jump)."""
        g = self._gap()
        self._pending.append(ObstacleSpawn(self.next_x, 'maw'))
        self._pending.append(ObstacleSpawn(self.next_x + g, 'arch'))
        self._pending.append(ObstacleSpawn(self.next_x + g * 2, 'maw'))
        self.next_x += g * 2 + self._sentence_gap()

    def _gen_sentence(self):
        """Pick and generate a random sentence from the full pool."""
        kind = random.choice(list(SentenceKind))
        if kind == SentenceKind.STUTTER:
            self._gen_stutter()
        elif kind == SentenceKind.WHIP:
            self._gen_whip()
        elif kind == SentenceKind.GAMBLE:
            self._gen_gamble()
        elif kind == SentenceKind.CHORD:
            self._gen_chord()
        elif kind == SentenceKind.CROSS:
            self._gen_cross()

    # -- public API --

    def update_difficulty(self, score: int):
        self.difficulty = min(1.0, score / 500.0)

    def get_spawns(self, world_offset: float) -> List[ObstacleSpawn]:
        """Generate sentences ahead of the scroll, return ready-to-spawn obstacles."""
        spawn_horizon = world_offset + COLS + SPAWN_LOOKAHEAD

        while self.next_x < spawn_horizon:
            self._gen_sentence()

        emit = [o for o in self._pending if o.world_x < spawn_horizon]
        self._pending = [o for o in self._pending if o.world_x >= spawn_horizon]
        return emit

    def reset(self):
        """Fresh start — always begins with The Stutter."""
        self.__init__()


# ============================================================
# DEATH SEQUENCE
# ============================================================

class DeathPhase(Enum):
    FREEZE = auto()       # 5 frames — player explodes
    FADE = auto()         # 10 frames — screen to black
    FORMATION = auto()    # 60 frames — roses materialize
    EPITAPH = auto()      # frames 60-120 — message flickers
    WAIT = auto()         # spacebar to restart

@dataclass
class DeathParticle:
    x: float
    y: float
    vx: float
    vy: float
    symbol: str
    color: str
    life: int

@dataclass
class DeathRose:
    x: int
    y: int
    symbol: str
    spawn_frame: int       # frame in FORMATION phase when this appears

EPITAPHS = [
    "MORTAL FLESH OBEYS ETERNAL MACHINERY.",
    "SYSTEM PURGED.",
    "YOU CANT KILL WHAT IS NOT ALIVE",
    "ALL LIVING MUST REPENT.",
    "GEARS OF HELL KEEP TURNING.",
    "THE CATHEDRAL REMAINS.",
    "HEX-COMMUNICADO.",
    "HOLY DATA CORRUPTED.",
    "ABYSS GAZES BACK",
    "SUCCUMB TO DESPAIR",
    "CORRUPT MESSIAH UNFAZED",
    "WITH COURAGE, COMES ALONG DEATH",
    "BREAK THY CHAINS",
    "WHY RESIST IRRESISTABLE ?"
]


# ============================================================
# GAME
# ============================================================

class Game:
    """Top-level state machine. Owns player, obstacles, map gen, and death sequence."""

    def __init__(self):
        self.world_offset: float = 0.0
        self.score: int = 0
        self._playing: bool = True
        self._frame: int = 0

        # entities
        self.player = Player()
        self.obstacles: List = []
        self.map_gen = MapGenerator()

        # death state
        self.phase = DeathPhase.WAIT
        self.death_timer: int = 0
        self.death_frame: int = 0
        self.death_particles: List[DeathParticle] = []
        self.death_roses: List[DeathRose] = []
        self.death_epitaph: str = ""

        # title-screen flag
        self._show_title: bool = False

    @property
    def playing(self) -> bool:
        return self._playing

    @property
    def show_title(self) -> bool:
        return self._show_title

    # -- main update --

    def update(self, actions: Actions):
        self._frame += 1
        if self._playing:
            self._update_playing(actions)
        else:
            self._update_death()

    # -- playing --

    def _update_playing(self, actions: Actions):
        self.player.update(actions)

        # Dash: instant teleport
        dash_burst = 0.0
        if self.player.dashing and self.player.dash_timer == DASH_DURATION - 1:
            dash_burst = DASH_TILES

        jump_boost = JUMP_HORIZONTAL if not self.player.grounded else 0.0
        self.world_offset += SCROLL_PER_TICK + dash_burst + jump_boost
        self.score = int(self.world_offset)

        # difficulty
        self.map_gen.update_difficulty(self.score)

        # spawn obstacles
        for sp in self.map_gen.get_spawns(self.world_offset):
            if sp.kind == 'maw':
                self.obstacles.append(GroundMaw(sp.world_x))
            elif sp.kind == 'air':
                obs = AirThreat(sp.world_x)
                if sp.center_y is not None:
                    obs.center_y = sp.center_y
                self.obstacles.append(obs)
            elif sp.kind == 'arch':
                self.obstacles.append(Archway(sp.world_x, self.map_gen.osc_speed))

        # update & cull
        for o in self.obstacles:
            o.update()
        cull_x = self.world_offset - 10
        self.obstacles = [o for o in self.obstacles
                          if getattr(o, 'world_x', 0) > cull_x]
        self.obstacles = [o for o in self.obstacles
                          if not isinstance(o, GroundMaw) or o.alive]

        # collision
        if not self.player.immune:
            px = PLAYER_X
            py = self.player.tile_y
            for o in self.obstacles:
                # ducking on ground → immune to air threats
                if isinstance(o, AirThreat) and self.player.grounded and self.player.ducking:
                    continue
                if o.collides_with(px, py, self.world_offset):
                    self._kill()
                    return

    # -- death --

    def _kill(self):
        self._playing = False
        self.phase = DeathPhase.FREEZE
        self.death_frame = 0
        self.player.alive = False

        px = PLAYER_X
        py = self.player.tile_y

        # 8-10 explosion particles
        count = random.randint(8, 10)
        for _ in range(count):
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(0.8, 2.5)
            self.death_particles.append(DeathParticle(
                x=float(px), y=float(py),
                vx=math.cos(angle) * speed,
                vy=math.sin(angle) * speed,
                symbol=random.choice(['*', '+']),
                color=random.choice(['\033[96m', '\033[95m', '\033[91m', '\033[97m']),
                life=5,
            ))

        # roses & symbols — 20 items, one every 3 frames over 60 frames
        rose_symbols = ['{@}', '@)-,-', '⸸', '⸸', '†', '+', '*']
        for i in range(20):
            self.death_roses.append(DeathRose(
                x=random.randint(4, COLS - 10),
                y=random.randint(1, ROWS - 3),
                symbol=random.choice(rose_symbols),
                spawn_frame=i * 3,
            ))

        self.death_epitaph = random.choice(EPITAPHS)

    def _update_death(self):
        self.death_frame += 1

        if self.phase == DeathPhase.FREEZE:
            # Update particles (fade over 5 frames)
            for p in self.death_particles:
                p.x += p.vx
                p.y += p.vy
                p.life -= 1
            if self.death_frame >= 5:
                self.phase = DeathPhase.FADE
                self.death_frame = 0

        elif self.phase == DeathPhase.FADE:
            if self.death_frame >= 10:
                self.phase = DeathPhase.FORMATION
                self.death_frame = 0

        elif self.phase == DeathPhase.FORMATION:
            if self.death_frame >= 60:
                self.phase = DeathPhase.EPITAPH
                self.death_frame = 0

        elif self.phase == DeathPhase.EPITAPH:
            if self.death_frame >= 60:
                self.phase = DeathPhase.WAIT

    def handle_death_input(self, action: str) -> bool:
        """Return True if player pressed space (signals title screen)."""
        if self.phase == DeathPhase.WAIT and action == 'jump':
            self._show_title = True
            return True
        return False

    def restart(self):
        self.__init__()
        # Fresh start: begin with The Stutter per spec
        self.map_gen.reset()
