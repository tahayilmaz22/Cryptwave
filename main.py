"""Cryptwave: Digital Exorcism — Entry Point

A neon-drenched endless runner in the terminal.
Synthwave meets gothic horror. The church got hacked by demons.

Usage:
    python main.py
"""

import sys
import time
import signal
import os

from engine import Game, FPS, Actions, COLS, ROWS
from display import (
    setup_terminal, restore_terminal, get_terminal_size,
    poll_input, actions_from_input, Renderer,
)

_HERE = os.path.dirname(os.path.abspath(__file__))
HIGHSCORE_FILE = os.path.join(_HERE, 'highscore.txt')


def load_highscore() -> int:
    """Read the persisted highscore from disk. Returns 0 if none found."""
    try:
        with open(HIGHSCORE_FILE, 'r') as f:
            return int(f.read().strip())
    except (FileNotFoundError, ValueError):
        return 0


def save_highscore(score: int):
    """Persist a new highscore to disk."""
    try:
        with open(HIGHSCORE_FILE, 'w') as f:
            f.write(str(score))
    except OSError:
        pass


def main():
    setup_terminal()
    cols, rows = get_terminal_size()

    if cols < COLS or rows < ROWS:
        restore_terminal()
        print(f"Terminal too small. Minimum {COLS}x{ROWS} required.")
        sys.exit(1)

    game = Game()
    renderer = Renderer()
    frame_time = 1.0 / FPS
    running = True
    highscore = load_highscore()

    def on_sigint(sig, frame):
        nonlocal running
        running = False
    signal.signal(signal.SIGINT, on_sigint)

    # ── title screen ──
    _draw_title(renderer, highscore)
    _wait_for_start()

    # ── main loop ──
    accumulator = 0.0
    last_time = time.perf_counter()

    while running:
        current = time.perf_counter()
        elapsed = current - last_time
        last_time = current
        if elapsed > 0.1:
            elapsed = 0.1
        accumulator += elapsed

        while accumulator >= frame_time:
            actions = actions_from_input()
            if actions.quit:
                running = False
                break

            if not game.playing:
                if game.handle_death_input(
                    'jump' if actions.jump else
                    'dash' if actions.dash else ''
                ):
                    pass
                else:
                    game.update(actions)
            else:
                game.update(actions)

            accumulator -= frame_time

        # ── return to title after death ──
        if game.show_title:
            if game.score > highscore:
                highscore = game.score
                save_highscore(highscore)
            _draw_title(renderer, highscore)
            _wait_for_start()
            game.restart()
            accumulator = 0.0
            last_time = time.perf_counter()
            continue

        renderer.render(game)

        remaining = frame_time - (time.perf_counter() - current)
        if remaining > 0.001:
            time.sleep(remaining * 0.5)

    # ── cleanup ──
    restore_terminal()
    score = game.score
    print(f"Final distance: {score}")
    if score < 100:
        print("THE CHURCH REMAINS CORRUPTED.")
    elif score < 300:
        print("A DIGITAL MARTYR.")
    elif score < 600:
        print("THE DEMONS ARE BANISHED.")
    else:
        print("LEGENDARY EXORCIST.")


def _draw_title(renderer: Renderer, highscore: int = 0):
    r = renderer
    r._frame += 1
    r.buf.clear()

    title = "CRYPTWAVE"
    subtitle = "terminal-cancer"

    r.buf.put_str((COLS - len(title)) // 2, ROWS // 2 - 4,
                  title, '\033[96m\033[1m', z=20)
    r.buf.put_str((COLS - len(subtitle)) // 2, ROWS // 2 - 2,
                  subtitle, '\033[95m', z=20)

    # Highscore
    if highscore > 0:
        hs_text = f"HIGHSCORE: {highscore:04d}"
        r.buf.put_str((COLS - len(hs_text)) // 2, ROWS // 2,
                      hs_text, '\033[93m', z=20)

    hint = "[ PRESS SPACE TO BEGIN ]"
    r.buf.put_str((COLS - len(hint)) // 2, ROWS // 2 + 2,
                  hint, '\033[2m', z=20)

    controls = [
        " SPACE / UP / W  — Jump ",
        " DOWN / S        — Duck / Cancel ",
        " RIGHT / D       — Dash (immunity + burst) ",
        " ESC / Ctrl+C    — Quit ",
    ]
    for i, ctrl in enumerate(controls):
        r.buf.put_str((COLS - len(ctrl)) // 2,
                      ROWS // 2 + 4 + i, ctrl, '\033[37m', z=20)

    r.buf.flush()


def _wait_for_start():
    while True:
        key = poll_input()
        if key == 'jump':
            return
        if key == 'quit':
            restore_terminal()
            sys.exit(0)
        time.sleep(0.02)


if __name__ == '__main__':
    main()
