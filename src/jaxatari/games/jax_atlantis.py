import os
from dataclasses import dataclass, field

from jax import config, Array
from jax._src.dtypes import dtype
import jax.lax
import jax.numpy as jnp
import chex
from numpy import array
import pygame
from typing import Dict, Any, Optional, NamedTuple, Tuple
from functools import partial

from jaxatari.rendering import atraJaxis as aj
from jaxatari.renderers import AtraJaxisRenderer
from jaxatari.environment import JaxEnvironment, JAXAtariAction as Action

from jax import debug  # TODO Remove Debug import later

@dataclass(frozen=True)
class GameConfig:
    """Game configuration parameters"""

    screen_width: int = 160
    screen_height: int = 250
    scaling_factor: int = 3
    bullet_height: int = 1
    bullet_width: int = 1
    bullet_speed: int = 3 #for side cannon 3 in x 2 in y middle 3 in y
    cannon_height: int = 8
    cannon_width: int = 8
    cannon_y: int = 160
    cannon_x: jnp.ndarray = field(
        default_factory=lambda: jnp.array([0,72,152], dtype=jnp.int32)
    )
    max_bullets: int = 2
    max_enemies: int = 20  # max 1 per line
    fire_cooldown_frames: int = 9  # delay between shots
    # y-coordinates of the different enemy paths/heights
    enemy_paths: jnp.ndarray = field(
        default_factory=lambda: jnp.array([60, 80, 100, 120], dtype=jnp.int32)
    )
    enemy_width: int = 15 # 3 different lengths 15, 16, 9
    enemy_height: int = 8
    enemy_speed: int = 1 # changes throughout the game
    enemy_spawn_min_frames: int = 5
    enemy_spawn_max_frames: int = 50
    wave_end_cooldown: int = 150 # cooldown of 150 frames after wave-end, before spawning new enemies
    wave_start_enemy_count: int = 10 # number of enemies in the first wave


# Each value of this class is a list.
# e.g. if i have 3 entities, then each of these lists would have a length of 3
class EntityPosition(NamedTuple):
    x: jnp.ndarray
    y: jnp.ndarray
    width: jnp.ndarray
    height: jnp.ndarray


class AtlantisState(NamedTuple):
    score: chex.Array # tracks the current score
    score_spent: chex.Array # tracks how much was spent on repair
    wave: chex.Array #tracks which wave we are in

    # columns = [ x,  y,  dx,   type_id, lane, active_flag ]
    #   x, y        → position
    #   dx          → horizontal speed (positive or negative)
    #   type_id     → integer index into your enemy_specs dict
    #   lane        → current lane the enemy is on
    #   active_flag → 1 if on-screen, 0 otherwise
    enemies: chex.Array  # shape: (max_enemies, 6)

    # columns = [ x, y, dx, dy]. dx and dy is the velocity
    bullets: chex.Array  # shape: (max_bullets, 4)
    bullets_alive: chex.Array  # stores all the active bullets as bools
    fire_cooldown: chex.Array  # frames left until next shot
    fire_button_prev: chex.Array  # was fire button down last frame
    enemy_spawn_timer: chex.Array  # frames until next spawn
    rng: chex.Array  # PRNG state
    lanes_free: chex.Array # bool for each lane
    command_post_alive: chex.Array # is command post alive (middle cannon)
    number_enemies_wave_remaining: chex.Array # number of remaining enemies per wave
    wave_end_cooldown_remaining: chex.Array
    #installations: chex.Array # should store all current installations and their coordinates
    plasma_x: chex.Array # X position of the plasma. Y position is actually not that relevant
    # shape==(max_enemies,) 1=that slot may draw beam. Plasma is deactivated once a cannon is hit, after that the enemy
    # should reach the end of the screen until it is reactivated. See _refresh_plasma_allowed for more info.
    plasma_allowed: chex.Array
    cannons_alive: chex.Array # shape (3,), one flag per cannon

class AtlantisObservation(NamedTuple):
    score: jnp.ndarray
    enemy: EntityPosition
    bullet: EntityPosition


class AtlantisInfo(NamedTuple):
    time: jnp.ndarray


class Renderer_AtraJaxis(AtraJaxisRenderer):
    sprites: Dict[str, Any]

    def __init__(self, config: GameConfig | None = None):
        super().__init__()
        self.config = config or GameConfig()
        self.sprite_path = (
            f"{os.path.dirname(os.path.abspath(__file__))}/sprites/atlantis"
        )
        self.sprites = self._load_sprites()

    def _load_sprites(self) -> dict[str, Any]:
        """Loads all necessary sprites from .npy files."""
        sprites: Dict[str, Any] = {}

        # Helper function to load a single sprite frame
        def _load_sprite_frame(name: str) -> Optional[chex.Array]:
            path = os.path.join(self.sprite_path, f"{name}.npy")
            frame = aj.loadFrame(path)
            if isinstance(frame, jnp.ndarray) and frame.ndim >= 2:
                return frame.astype(jnp.uint8)

        # Load Sprites
        # Backgrounds + Dynamic elements + UI elements
        sprite_names = [
            # 'background_0', 'background_1', 'background_2',
        ]
        for name in sprite_names:
            loaded_sprite = _load_sprite_frame(name)
            if loaded_sprite is not None:
                sprites[name] = loaded_sprite

        return sprites

    @partial(jax.jit, static_argnums=(0,))
    def render(self, state: AtlantisState) -> chex.Array:

        def _solid_sprite(
            width: int, height: int, rgb: tuple[int, int, int]
        ) -> chex.Array:
            """Creates a slid-color RGBA sprite of given size and color"""
            rgb_arr = jnp.broadcast_to(
                jnp.array(rgb, dtype=jnp.uint8), (width, height, 3)
            )
            alpha = jnp.full((width, height, 1), 255, dtype=jnp.uint8)
            return jnp.concatenate([rgb_arr, alpha], axis=-1)  # (W, H, 4)

        cfg = self.config
        W, H = cfg.screen_width, cfg.screen_height

        # add black background
        BG_COLOUR = (0, 0, 0)
        bg_sprite = _solid_sprite(W, H, BG_COLOUR)
        # render black rectangle at (0,0)
        raster = aj.render_at(jnp.zeros_like(bg_sprite[..., :3]), 0, 0, bg_sprite)

        # add deep blue cannons
        cannon_sprite = _solid_sprite(cfg.cannon_width, cfg.cannon_height, (0, 62, 120))

        @partial(jax.jit, static_argnums=(0,))
        def _draw_cannon(i, ras):
            alive = state.cannons_alive[i]
            x0 = cfg.cannon_x[i]
            y0 = cfg.cannon_y

            def _blit(r):
                return aj.render_at(r, x0, y0, cannon_sprite)

            return jax.lax.cond(alive, _blit, lambda r: r, ras)

        raster = jax.lax.fori_loop(0, cfg.cannon_x.shape[0], _draw_cannon, raster)

        # add solid white cannons
        bullet_sprite = _solid_sprite(
            cfg.bullet_width, cfg.bullet_height, (255, 255, 255)
        )

        def _draw_bullet(i, ras):
            alive = state.bullets_alive[i]
            bx, by = state.bullets[i, 0], state.bullets[i, 1]
            return jax.lax.cond(
                alive,
                lambda r: aj.render_at(r, bx, by, bullet_sprite),
                lambda r: r,
                ras,
            )

        raster = jax.lax.fori_loop(0, cfg.max_bullets, _draw_bullet, raster)

        # add red enemies
        enemy_sprite = _solid_sprite(cfg.enemy_width, cfg.enemy_height, (255, 0, 0))

        def _draw_enemy(i, ras):
            active = state.enemies[i, 5] == 1
            ex = state.enemies[i, 0].astype(jnp.int32)
            ey = state.enemies[i, 1].astype(jnp.int32)
            flip = state.enemies[i, 2] < 0  # dx < 0 -> facing left

            def _do(r):
                return aj.render_at(r, ex, ey, enemy_sprite, flip_horizontal=flip)

            return jax.lax.cond(active, _do, lambda r: r, ras)

        raster = jax.lax.fori_loop(0, cfg.max_enemies, _draw_enemy, raster)

        def _handle_draw_plasma(i, ras):
            # Plasma beam logic
            plasma_color = (0, 255, 255)  # cyan
            beam_pixel = _solid_sprite(1, 1, plasma_color)
            plasma_height = cfg.cannon_y

            # only for the one enemy in lane 3 (4th lane) and active
            # Note that the plasma is deactivated once a cannon is hit, unless it reaches the end of screen.
            on_lane4 = (state.enemies[i, 4] == 3) & (state.enemies[i, 5] == 1)
            may_shoot = state.plasma_allowed[i]
            ex       = state.enemies[i, 0].astype(jnp.int32) + cfg.enemy_width/2
            ey       = state.enemies[i, 1].astype(jnp.int32)
            start_y  = ey + cfg.enemy_height

            # draw vertical line pixel-by-pixel
            def _draw_row(y, r):
                return jax.lax.cond(
                  on_lane4 & (y >= start_y) & may_shoot,
                  lambda rr: aj.render_at(rr, ex, y, beam_pixel),
                  lambda rr: rr,
                  r,
                )

            # loop y=0…screen_h−1
            return jax.lax.fori_loop(0, plasma_height, _draw_row, ras)

        # apply to every enemy slot (only one will actually fire)
        raster = jax.lax.fori_loop(0, cfg.max_enemies, _handle_draw_plasma, raster)
        # ————————————————————————————————

        return raster


class JaxAtlantis(JaxEnvironment[AtlantisState, AtlantisObservation, AtlantisInfo]):
    def __init__(
        self,
        frameskip: int = 1,
        reward_funcs: list[callable] = None,
        config: GameConfig | None = None,
    ):
        super().__init__()
        # if no config was provided, instantiate the default one
        self.config = config or GameConfig()
        self.frameskip = frameskip
        self.frame_stack_size = 4
        if reward_funcs is not None:
            reward_funcs = tuple(reward_funcs)
        self.reward_funcs = reward_funcs
        self.action_set = [
            Action.NOOP,
            Action.FIRE,  # centre cannon
            Action.LEFTFIRE,  # left cannon
            Action.RIGHTFIRE,  # right cannon
        ]

    def reset(
        self, key: jax.random.PRNGKey = jax.random.PRNGKey(42)
    ) -> Tuple[AtlantisObservation, AtlantisState]:
        # --- empty tables ---
        empty_enemies = jnp.zeros((self.config.max_enemies, 6), dtype=jnp.int32)
        empty_bullets = jnp.zeros((self.config.max_bullets, 4), dtype=jnp.int32)
        empty_bullets_alive = jnp.zeros((self.config.max_bullets,), dtype=jnp.bool_)
        empty_lanes = jnp.ones((4,), dtype=jnp.bool_)

        # split the PRNGkey so we get one subkey for the spawn-timer and one to carry forward in state.rng
        key, sub = jax.random.split(key)

        # initial state
        new_state = AtlantisState(
            score=jnp.array(0, dtype=jnp.int32),
            score_spent=jnp.array(0, dtype=jnp.int32),
            wave=jnp.array(0, dtype=jnp.int32), # start with wave-number 0
            enemies=empty_enemies,
            bullets=empty_bullets,
            bullets_alive=empty_bullets_alive,
            fire_cooldown=jnp.array(0, dtype=jnp.int32),
            fire_button_prev=jnp.array(False, dtype=jnp.bool_),
            enemy_spawn_timer=jax.random.randint(
                sub,
                (),
                self.config.enemy_spawn_min_frames,
                self.config.enemy_spawn_max_frames + 1,
                dtype=jnp.int32,
            ),
            rng=key,
            lanes_free=empty_lanes,
            command_post_alive=jnp.array(True, dtype=jnp.bool_),
            number_enemies_wave_remaining=jnp.array(self.config.wave_start_enemy_count, dtype=jnp.int32),
            wave_end_cooldown_remaining=jnp.array(0, dtype=jnp.int32),
            plasma_x=jnp.array(-1, dtype=jnp.int32),
            plasma_allowed=jnp.ones((self.config.max_enemies,), dtype=jnp.bool_),
            cannons_alive=jnp.array([True, True, True], dtype=jnp.bool_)
        )

        obs = self._get_observation(new_state)
        return obs, new_state

    def _interpret_keyboard_action(self, state, action) -> Tuple[bool, bool, int]:
        """
        Translate action into control signals
        Returns three vars:

        fire_pressed: If any button is currently pressed
        can_shoot: cooldown expired and just pressed a button
        cannon_idx: (0) left, (1) centre, (2) right or -1.
        """
        fire_pressed = (
            (action == Action.LEFTFIRE)
            | (action == Action.FIRE)
            | (action == Action.RIGHTFIRE)
        )
        # It is important to keep track if the button just got pressed
        # to prevent holding the button down and spamming bullets
        just_pressed = fire_pressed & (~state.fire_button_prev)
        can_shoot = (state.fire_cooldown == 0) & just_pressed

        cannon_idx = jnp.where(
            can_shoot,
            jnp.where(
                action == Action.LEFTFIRE,
                0,
                jnp.where(
                    action == Action.FIRE,
                    1,
                    jnp.where(action == Action.RIGHTFIRE, 2, -1),
                ),
            ),
            -1,
        )
        return fire_pressed, cannon_idx

    # ..................................................................

    def _spawn_bullet(self, state, cannon_idx):
        """Insert newly spawned bullet in first free slot"""
        cfg = self.config

        def _do_spawn(s):
            # To identify which slots are free
            # bullets_alive is a boolean array. If an entry is true, then it holds an active bullet
            # ~ inverts the boolean array, such that a slot is free, when bullets_alive[i] == False
            free_slots = ~s.bullets_alive
            slot_available = jnp.any(free_slots)  # at least one free?
            slot_idx = jnp.argmax(free_slots)  # first free slot

            # horizontal component dx:
            # - if cannon_idx == 0 (left), shoot rightwards -> +bullet_speed
            # - if cannond_idx == 2 (right), shoot leftwards -> -bullet_speed
            # else go straigt -> 0
            dx = jnp.where(
                cannon_idx == 0,  # true for left cannon
                cfg.bullet_speed,  # e.g. +3 pixels/frame
                jnp.where(
                    cannon_idx == 2,  # true for right cannon
                    -cfg.bullet_speed,  # e.g. -3 px
                    0,  # zero horizontal velocity
                ),
            )

            # vertical component dy:
            # - side bullets move slightly slower up than middle bullet Because origin is in top left, its negative
            dy = jnp.where(jnp.logical_or(cannon_idx == 0, cannon_idx==2), -(cfg.bullet_speed-1), -cfg.bullet_speed)

            new_bullet = jnp.array(
                [cfg.cannon_x[cannon_idx], cfg.cannon_y, dx, dy],  # velocity
                dtype=jnp.int32,
            )

            # write into state
            def _write(s2):
                b2 = s2.bullets.at[slot_idx].set(new_bullet)
                a2 = s2.bullets_alive.at[slot_idx].set(True)
                return s2._replace(bullets=b2, bullets_alive=a2)

            # Conditionally write if a free slot exists
            return jax.lax.cond(slot_available, _write, lambda x: x, s)

        # Only attempt the spawn when a cannon actually fired this frame
        alive = jnp.where(cannon_idx >= 0, state.cannons_alive[cannon_idx], False)
        return jax.lax.cond(alive, _do_spawn, lambda x: x, state)

    def _update_cooldown(self, state, cannon_idx):
        """Reset after a shot or decrement the fire cooldown timer."""
        cfg = self.config
        new_cd = jnp.where(
            cannon_idx >= 0,  # -1 means no cannon fired
            jnp.array(cfg.fire_cooldown_frames, dtype=jnp.int32),
            jnp.maximum(state.fire_cooldown - 1, 0),
        )
        return state._replace(fire_cooldown=new_cd)

    def _move_bullets(self, state):
        """Move bullets by their velocity and deactivate offscreen bullets"""
        cfg = self.config

        # compute new x and y positions by adding the velocity dx and dy
        # state.bullets has shape (max_bullets, 4): (x,y,dx,dy)
        # [:, :2] takes all rows, but only columns 0 and 1 which are x and y
        # 2:4 then is dx and dy
        positions = state.bullets[:, :2] + state.bullets[:, 2:4]
        # Write updated position back into bullets array
        moved = state.bullets.at[:, :2].set(positions)

        # check if bullets are still onscreen
        in_bounds = (
            (positions[:, 0] >= 0)
            & (positions[:, 0] < cfg.screen_width)
            & (positions[:, 1] >= 0)
            & (positions[:, 1] < cfg.screen_height)
        )

        # a bullet only remains alive if it was already alive and still on-screen
        alive = state.bullets_alive & in_bounds
        return state._replace(bullets=moved, bullets_alive=alive)

    @staticmethod
    @jax.jit
    def _sample_speed(rng, wave):
        """
        Returns speed with a long left tail (mostly slow, rare fast).
        Uses a geometric distribution, shifting as wave increases.
        """
        # Higher waves -> lower p -> more fast enemies
        base_p = 0.8

        # adjust probability p for the geometric distribution
        # clip p to always stay between 0.3 and 0.95
        p = jnp.clip(base_p - 0.03 * wave, 0.3, 0.95)
        speed = jax.random.geometric(rng, p)
        max_speed = wave + 1 # limit speed. wave=0 -> max speed 1. wave=1 -> 2,...
        return jnp.minimum(speed, max_speed)

    @partial(jax.jit, static_argnums=(0,))
    def _spawn_enemy(self, state: AtlantisState) -> AtlantisState:
        """
        • Decrement spawn-timer every frame if lane is free
        • When it reaches 0, try to insert one enemy into the first free
          slot of state.enemies
        • set to first lane
        • Pick direction with prng
        • After spawning (or if the screen is full) reset the timer to a
          new random value in min, max and advance the rng
        """

        cfg = self.config

        # helper that creates  a fresh timer value
        def _next_timer(rng):
            """Draw new integer in min, max inclusive"""
            return jax.random.randint(
                rng,
                (),
                cfg.enemy_spawn_min_frames,
                cfg.enemy_spawn_max_frames + 1,
                dtype=jnp.int32,
            )
        # check if the first lane is free
        lane_free = state.lanes_free[0]
        # Count down the timer if lane is free
        timer = jnp.where(lane_free, state.enemy_spawn_timer - 1, state.enemy_spawn_timer)

        # Split the current PRNG key into two new, independent keys
        #   rng_spawn will be used to draw random values for spawning enemies
        #   rng_after will be stored for the next frame’s randomness
        rng_spawn, rng_speed, rng_after = jax.random.split(state.rng, 3)

        # if the timer is still bigger than 0, just update the timer and rng state
        def _no_spawn(s: AtlantisState) -> AtlantisState:
            return s._replace(enemy_spawn_timer=timer, rng=rng_after)

        def _spawn(s: AtlantisState) -> AtlantisState:

            # enemy has 5 entries, the last one (index 5) is the active_flag
            # if this value is 0, it means an enemy isn't active anymore
            # this can be because he either left the screen, or he was shot
            # the code returns a boolean array (active_flag == 0 -> true)
            free_slots = s.enemies[:, 5] == 0
            # check if at least one entry is true
            have_slot = jnp.any(free_slots)
            # get free slot index
            slot_idx = jnp.argmax(free_slots)

            # Choose a lane (rows in cfg.enemy_paths) and a direction.
            lane_idx = 0
            lane_y = cfg.enemy_paths[lane_idx]

            # randomy decide the direction of the enemies, left or right
            go_left = jax.random.bernoulli(rng_spawn)  # True == left
            # iif go_left is True, then set start x to the window_size + enemy_width
            # this ensures, that the enemy will spawn outside the visible area
            # if the value is false, spawn outside the visible area on the left side
            start_x = jnp.where(
                go_left,
                cfg.screen_width,
                -cfg.enemy_width,
            )
            # Set the direction
            speed = self._sample_speed(rng_speed, s.wave)
            dx = jnp.where(go_left, -speed, speed)
            # dx = jnp.where(go_left, -cfg.enemy_speed, cfg.enemy_speed)

            # assemble the enemy. for now  the type will always be 0
            # TODO: change later
            # also sets the enemy to be active (last entry is 1)
            new_enemy = jnp.array(
                [start_x, lane_y, dx, 0, 0, 1],
                dtype=jnp.int32,
            )

            def _write(write_s):
                updated_enemies = write_s.enemies.at[slot_idx].set(new_enemy)
                return write_s._replace(enemies=updated_enemies)

            # if enemies still has an empty slot, then write the new enemy
            # otherwise leave the state unchanged
            updated_state = jax.lax.cond(have_slot, _write, lambda x: x, s)

            # reset the timer
            new_timer = _next_timer(rng_after)
            # set first lane to full
            new_lanes = s.lanes_free.at[0].set(False)

            # decrease counter of spawnable enemies per wave
            updated_state = updated_state._replace(number_enemies_wave_remaining=(s.number_enemies_wave_remaining - 1))
            # jax.debug.print(f"Enemies remaining: {updated_state.number_enemies_wave_remaining}")

            return updated_state._replace(enemy_spawn_timer=new_timer, rng=rng_after, lanes_free=new_lanes)

        spawn_allowed = (timer <= 0) & (state.number_enemies_wave_remaining > 0)
        return jax.lax.cond(
            spawn_allowed,  # condition
            _spawn,  # If there are remaining enemies in the wave and time is 0, spawn
            _no_spawn,  # do not spawn
            state,
        )  # operands for the two functions

    # move all active enemies horizontally and deactive off-screen ones
    @partial(jax.jit, static_argnums=(0,))
    def _move_enemies(self, state: AtlantisState) -> AtlantisState:
        cfg = self.config
        enemies = state.enemies
        x_pos = enemies[:, 0]
        y_pos = enemies[:, 1]
        dx_vel = enemies[:, 2]
        lane_indices = enemies[:, 4] # get current lanes of all enemies
        is_active = enemies[:, 5] == 1 # get active flags of all enemies
        number_lanes = cfg.enemy_paths.shape[0]

        # y always stays constant. just move x by adding dx
        new_pos = x_pos + dx_vel  # x + dx
        #-- enemies = x_pos.set(new_pos)  # write back

        # decide if an enemy is still on_screen
        # as long as a part of the enemy is still in the viewable area, the enemy stays alive
        # 1) check right edge > 0 -> enemies right edge hasnt completely passed the left edge of the screen
        # 2) check left edge < screen_width -> enemies left edge hasnt gone past the right edge of the screen
        on_screen = (new_pos + cfg.enemy_width > 0) & (new_pos < cfg.screen_width)

        # Identify enemies that are NOT on screen
        off_screen_enemies = ~on_screen
        inactive_enemies = ~is_active

        # identify all the enemies that move left (dx negative)
        # used for wrap-around/respawn position
        # if negative, spawn on right side. Otherwise on left side
        # but with an offset of enemy_width
        respawn_x = jnp.where(
            dx_vel < 0,
            cfg.screen_width,
            -cfg.enemy_width
        )

        # create array of length max_enemies
        # If the enemy is still active, it's lane-id gets incremented by 1
        # otherwise the lane gets set to 0
        # stores id of next lane for each enemy
        next_lanes = jnp.where(
            is_active,
            lane_indices + 1,
            0 # set dummy 0
        )

        # Determine which enemies are allowed to advance into the next lane:
        # - Inactive enemies are always allowed to advance (the dont block)
        # - If the enemy would advance past the last lane (next_lanes >= number_lanes), block this.
        #   THis ensures, they are correctly deactivated after the last lane
        # - For active enemies withing bounds, only allow if the target lane is currently free
        # This logic ensures that only one enemy  can occupy a lane at a time.
        # If a faster enemy reaches the end of its lane but the next lane is occupied,
        # it will "wait" until the next lane becomes available
        # This creates a queue-like behaviour
        next_lane_free = (
                inactive_enemies  # already inactive
                | (next_lanes >= number_lanes)  # past the last lane -> Enemy will be deactivated later
                | ((next_lanes < number_lanes) & state.lanes_free[next_lanes])  # only free lanes. Normal case
        )
        # Apply the new x positions only where off_screen_enemies is True
        # and for active enemies
        updated_x = jnp.where(
            (off_screen_enemies & next_lane_free & is_active),
            respawn_x,
            new_pos  # Keep original x positions for on-screen enemies
        )

        # Then update the lanes
        updated_lanes = jnp.where(
            (off_screen_enemies & next_lane_free),
            lane_indices + 1,
            lane_indices
        )

        # combine previous active flag with check for last lane
        # any enemy that's went through all four lanes gets deactivated
        flags = is_active & (updated_lanes < number_lanes)

        # Get the corresponding y-positions from enemy_paths
        lane_y_positions = jnp.where(
            updated_lanes < number_lanes,
            cfg.enemy_paths[updated_lanes],
            - cfg.enemy_height
        )

        updated_enemies = enemies.at[:,0].set(updated_x)
        updated_enemies = updated_enemies.at[:, 1].set(lane_y_positions)
        updated_enemies = updated_enemies.at[:,4].set(updated_lanes)
        updated_enemies = updated_enemies.at[:, 5].set(flags)


        #check if lanes are free now
        lane_masks = []
        # iterate through length of enemy_paths (4)
        # lane_mask checks if
        for i in range(len(cfg.enemy_paths)):
            # For each lane, check if any active enemy is in that lane
            lane_mask = (updated_enemies[:, 4] == i) & flags
            lane_is_occupied = jnp.any(lane_mask)
            lane_masks.append(~lane_is_occupied)  # True if lane is free

        free_lanes = jnp.array(lane_masks)
        return state._replace(enemies=updated_enemies, lanes_free=free_lanes)


    @partial(jax.jit, static_argnums=(0,))
    def _check_bullet_enemy_collision(self, state: AtlantisState) -> AtlantisState:
        """
        Collision check between bullets and enemies

        Each bulllet/enemy an axis-aligned rectangle. Now:
        1. compute the four edges (lefet, right, top, bottom) for every bullet and every enemy
        2. Build two (BxE) boolean matrices for x-overlap and y-overlap
        3. compute AND of the two matrices and build the hit_matrix[b,e]. An entry is true, when bullet b and enemy e overlap in both X and Y
        4. ignore inactive bullets/enemies (through masking)
        5. reduce hit_mtarix to per-bullet and per_enemy "was hit?" flags
        6. deactive those objects
        """
        cfg = self.config

        bullet_x, bullet_y = state.bullets[:, 0], state.bullets[:, 1]  # (B,)
        enemy_x, enemy_y = state.enemies[:, 0], state.enemies[:, 1]  # (E,)

        # compute edge coordinates  for all rectangles
        # broadcasting with none inserts singleton axes so every
        # bullet is paired with every enemy
        b_left = bullet_x[:, None]
        b_right = (bullet_x + cfg.bullet_width)[:, None]
        b_top = bullet_y[:, None]
        b_bottom = (bullet_y + cfg.bullet_height)[:, None]

        # Enemy edges
        e_left = enemy_x[None, :]
        e_right = (enemy_x + cfg.enemy_width)[None, :]
        e_top = enemy_y[None, :]
        e_bottom = (enemy_y + cfg.enemy_height)[None, :]

        # True where bullets left < enemies right AND bullets right >  enemies left
        overlap_x = (b_left < e_right) & (b_right > e_left)
        # ...
        overlap_y = (b_top < e_bottom) & (b_bottom > e_top)

        # True when both horizontal and vertical overlaps occur
        hit_matrix = overlap_x & overlap_y

        # Ignore inactive objects right away
        hit_matrix &= state.bullets_alive[:, None]
        hit_matrix &= (state.enemies[:, 5] == 1)[None, :]

        # check if bullet collided with any enemy
        bullet_hit = jnp.any(hit_matrix, axis=1)  # (B,)
        # check if enemy was hit by any bullet
        enemy_hit = jnp.any(hit_matrix, axis=0)  # (E,)

        # deactivate bullets and enemies
        new_bullet_alive = state.bullets_alive & (~bullet_hit)

        new_enemy_flags = (state.enemies[:, 5] == 1) & (~enemy_hit)
        enemies_updated = state.enemies.at[:, 5].set(new_enemy_flags.astype(jnp.int32))

        return state._replace(bullets_alive=new_bullet_alive, enemies=enemies_updated)



    @partial(jax.jit, static_argnums=(0,))
    def _update_wave(self, state: AtlantisState) -> AtlantisState:
        cfg = self.config

        def _new_wave(s: AtlantisState) -> AtlantisState:
            new_wave = s.wave + 1
            # jax.debug.print(f"Started wave {new_wave}")
            # compute how many enemies next wave should have
            next_count = cfg.wave_start_enemy_count + new_wave * 2
            return s._replace(
                wave=new_wave,
                wave_end_cooldown_remaining=jnp.array(cfg.wave_end_cooldown, jnp.int32),
                number_enemies_wave_remaining=next_count,
            )

        def _same_wave(s: AtlantisState) -> AtlantisState:
            return s

        # if no enemies are remaining and the screen is empty, start a new wave
        return jax.lax.cond(
            (state.number_enemies_wave_remaining == 0) & (~jnp.any(state.enemies[:, 5] == 1)),
            _new_wave,
            _same_wave,
            state,
        )

    @partial(jax.jit, static_argnums=(0,))
    def _cooldown_finished(self, state: AtlantisState) -> Array:
        return state.wave_end_cooldown_remaining == 0

    @partial(jax.jit, static_argnums=(0,))
    def _update_plasma_x_position(self, state: AtlantisState) -> AtlantisState:
        lane4 = (state.enemies[:, 4] == 3) & (state.enemies[:, 5] == 1)
        # compute each candidate beam-X (center of enemy)
        ex = state.enemies[:, 0] + (self.config.enemy_width // 2)
        # select beam-X for lane4 enemies, else -1
        beam_xs = jnp.where(lane4, ex, -1)
        # pick the max (will be -1 if no such enemy)
        plasma_x = jnp.max(beam_xs)
        # save into the state

        # Todo remove debug statement after collision logic implemented
        #debug.print("[DEBUG] plasma_x = {px}", px=plasma_x)

        return state._replace(plasma_x=plasma_x)

    def _handle_cannon_plasma_hit(self, state: AtlantisState) -> AtlantisState:
        # cannon positions (0=left,1=middle,2=right)
        cx = self.config.cannon_x                   # shape (3,)

        # which cannon center does the beam line up with?
        raw_hits = (state.plasma_x == cx)           # shape (3,), True for any hit

        # --- 1) figure out which cannon to skip based on spawn side ---
        # find shooter: the one in lane 4 whose beam_x == plasma_x
        lane4_mask  = (state.enemies[:, 4] == 3) & (state.enemies[:, 5] == 1)
        exs         = state.enemies[:, 0] + (self.config.enemy_width // 2)
        shooter_idx = jnp.argmax(lane4_mask & (exs == state.plasma_x))

        # check its direction: dx>0 means it came from the left (skip cannon 0),
        # dx<0 means it came from the right (skip cannon 2)
        dx          = state.enemies[shooter_idx, 2]
        skip_idx    = jnp.where(dx > 0, 0, 2)

        # build mask of “real” cannon hits:
        # – it lines up (raw_hits)
        # – cannon is still alive (state.cannons_alive)
        # – it’s *not* the skipped one (skip_idx)
        idxs        = jnp.arange(cx.shape[0], dtype=jnp.int32)
        real_hits   = state.plasma_allowed[shooter_idx] & raw_hits & state.cannons_alive & (idxs != skip_idx)

        # kill those cannons
        new_cannons_alive = state.cannons_alive & (~real_hits)

        # --- 2) only disable plasma from the shooter if it actually hit an alive cannon ---
        disable_plasma = state.plasma_allowed
        disable_plasma = jax.lax.cond(
            jnp.any(real_hits),
            lambda arr: arr.at[shooter_idx].set(False),
            lambda arr: arr,
            disable_plasma,
        )

        return state._replace(
            cannons_alive= new_cannons_alive,
            plasma_allowed= disable_plasma,
        )

    # Reactivate plasma after a cannon is hit and the end of screen is reached.
    @partial(jax.jit, static_argnums=(0,))
    def _refresh_plasma_allowed(self, state: AtlantisState) -> AtlantisState:
        new_pos = state.enemies[:,0]
        on_screen = (new_pos + self.config.enemy_width > 0) & (new_pos < self.config.screen_width)
        # whenever *off* screen, re-enable
        allowed = jnp.where(on_screen, state.plasma_allowed, True)
        return state._replace(plasma_allowed=allowed)

    @partial(jax.jit, static_argnums=(0,))
    def step(
        self, state: AtlantisState, action: chex.Array
    ) -> Tuple[AtlantisObservation, AtlantisState, float, bool, AtlantisInfo]:
        def _pause_step(s: AtlantisState) -> AtlantisState:
            # reduce pause cooldown
            s = s._replace(
                wave_end_cooldown_remaining=jnp.maximum(s.wave_end_cooldown_remaining - 1, 0)
            )
            s = self._move_bullets(s)
            return self._update_wave(s)

        def _wave_step(s: AtlantisState) -> AtlantisState:
            # input handling
            fire_pressed, cannon_idx = self._interpret_keyboard_action(s, action)

            # bullets
            s = self._spawn_bullet(s, cannon_idx)
            s = self._update_cooldown(s, cannon_idx) \
                ._replace(fire_button_prev=fire_pressed)

            # enemies
            s = self._spawn_enemy(s)

            # motion & collisions
            s = self._move_bullets(s)
            s = self._move_enemies(s)
            s = self._refresh_plasma_allowed(s)
            s = self._check_bullet_enemy_collision(s)
            s = self._update_plasma_x_position(s)
            s = self._handle_cannon_plasma_hit(s)

            # check if wave quota exhausted → start pause
            s = self._update_wave(s)
            return s

        state = jax.lax.cond(
            self._cooldown_finished(state),
            _wave_step,
            _pause_step,
            state
        )

        observation = self._get_observation(state)
        info = AtlantisInfo(time=jnp.array(0, dtype=jnp.int32))
        reward = 0.0  # Placeholder: no scoring yet
        done = False  # Never terminates for now

        return observation, state, reward, done, info

    @partial(jax.jit, static_argnums=(0,))
    def _get_observation(self, state: "AtlantisState") -> "AtlantisObservation":
        # just placeholders
        enemies_pos = EntityPosition(
            0,
            0,
            0,
            0,
        )

        bullets_pos = EntityPosition(
            0,
            0,
            0,
            0,
        )

        return AtlantisObservation(state.score, enemies_pos, bullets_pos)

    @partial(jax.jit, static_argnums=(0,))
    def _get_info(self, state: AtlantisState) -> AtlantisInfo:
        """
        Placeholder info: returns zero time and empty reward array.
        """
        return AtlantisInfo(
            time=jnp.array(0, dtype=jnp.int32),
        )

    @partial(jax.jit, static_argnums=(0,))
    def _get_reward(self, previous_state: AtlantisState, state: AtlantisState) -> float:
        """
        Placeholder reward: always zero.
        """
        return 0.0

    @partial(jax.jit, static_argnums=(0,))
    def _get_done(self, state: AtlantisState) -> bool:
        """
        Placeholder done: never terminates.
        """
        return False

    @partial(jax.jit, static_argnums=(0,))
    def get_action_space(self) -> jnp.ndarray:
        """
        Placeholder done: never terminates.
        """
        return jnp.array(self.action_set)


# Keyboard inputs
def get_human_action() -> chex.Array:
    keys = pygame.key.get_pressed()
    # up = keys[pygame.K_w] or keys[pygame.K_UP]
    # down = keys[pygame.K_s] or keys[pygame.K_DOWN]
    left = keys[pygame.K_a] or keys[pygame.K_LEFT]
    right = keys[pygame.K_d] or keys[pygame.K_RIGHT]
    fire = keys[pygame.K_SPACE]

    if right and fire:
        return jnp.array(Action.RIGHTFIRE)
    if left and fire:
        return jnp.array(Action.LEFTFIRE)
    if fire:
        return jnp.array(Action.FIRE)

    return jnp.array(Action.NOOP)


def main():
    config = GameConfig()
    pygame.init()
    pygame.font.init()

    screen = pygame.display.set_mode(
        (
            config.screen_width * config.scaling_factor,
            config.screen_height * config.scaling_factor,
        )
    )
    pygame.display.set_caption("Atlantis")
    clock = pygame.time.Clock()

    # prepare a font for the “GAME OVER” message
    font = pygame.font.SysFont(None, 48)

    game = JaxAtlantis(config=config)
    renderer = Renderer_AtraJaxis(config=config)
    jitted_step = jax.jit(game.step)
    jitted_reset = jax.jit(game.reset)

    # initial reset
    _, curr_state = jitted_reset()

    running = True
    game_over = False
    frame_by_frame = False
    frameskip = game.frameskip
    counter = 1

    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False

            # toggle frame-by-frame mode
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_f:
                frame_by_frame = not frame_by_frame

            # when paused, advance one frame on "N"
            elif frame_by_frame and event.type == pygame.KEYDOWN and event.key == pygame.K_n:
                if counter % frameskip == 0 and not game_over:
                    _, curr_state, _, _, _ = jitted_step(curr_state, get_human_action())

        # if not in frame-by-frame mode, step every frameskip
        if not frame_by_frame and counter % frameskip == 0 and not game_over:
            _, curr_state, _, _, _ = jitted_step(curr_state, get_human_action())

        # render
        raster = renderer.render(curr_state)
        aj.update_pygame(
            screen,
            raster,
            config.scaling_factor,
            config.screen_width,
            config.screen_height,
        )

        # check for game over (all cannons dead)
        if not game_over and not bool(jnp.any(curr_state.cannons_alive)):
            game_over = True
            # overlay "GAME OVER"
            text_surf = font.render("GAME OVER", True, (255, 0, 0))
            txt_rect = text_surf.get_rect(
                center=(
                    (config.screen_width * config.scaling_factor) // 2,
                    (config.screen_height * config.scaling_factor) // 2,
                )
            )
            screen.blit(text_surf, txt_rect)
            pygame.display.flip()
            pygame.time.delay(5000)  # wait 5 seconds
            running = False
            continue

        counter += 1
        clock.tick(60)

    pygame.quit()


if __name__ == "__main__":
    main()
