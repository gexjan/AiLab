import pygame
import sys
import random
import math
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Tuple, Dict
from math import pi
from functools import partial  # Add this import
import pygame
import jax

# --- Game Actions & Entity Types ---
class GameAction(Enum):
    SHOOT_LEFT = 0
    SHOOT_MIDDLE = 1
    SHOOT_RIGHT = 2
    NOOP = 3

class EntityType(Enum):
    BULLET = "bullet"
    ENEMY = "enemy"
    TOWER = "tower"
    TURRET = "turret"

# --- Configuration DataClasses ---
@dataclass(frozen=True)
class EntityDimensions:
    TOWER_WIDTH: int = 30
    TOWER_HEIGHT: int = 30
    BULLET_RADIUS: int = 5
    ENEMY_SIZE: int = 35

@dataclass(frozen=True)
class GamePhysicsConfig:
    BULLET_SPEED: float = 8.0
    ENEMY_SPEED_BASE: float = 2.0
    ENEMY_SPEED_INC: float = 0.5
    WAVE_SIZE: int = 8
    ENEMY_SPAWN_INTERVAL: int = 60  # frames
    FIRE_COOLDOWN: int = 15        # frames

@dataclass(frozen=True)
class AtlantisConfig:
    screen_width: int = 640
    screen_height: int = 480
    fps: int = 60
    tower_count: int = 4
    turret_count: int = 3

    # Just make the turret proportional to the launcher window. Turrets Should be at the bottom.
    turret_y: int = screen_height  - (screen_width - screen_height) / 5

    # Currently the tower and turret are the same height.
    #tower_y: int = 40
    tower_y: int = turret_y #TODO

    physics: GamePhysicsConfig = field(default_factory=GamePhysicsConfig)
    dimensions: EntityDimensions = field(default_factory=EntityDimensions)

@dataclass(frozen=True)
class RenderConfig:
    colors: Dict[str, Tuple[int,int,int]] = field(default_factory=lambda: {
        "background": (0, 0, 30),
        "tower": (100, 100, 200),
        "turret": (200, 200, 200),
        "bullet": (255, 255, 0),
        "enemy": (200, 50, 50),
        "text": (255, 255, 255),
    })
    font_size: int = 24

# --- Game Entities ---
@dataclass
class Bullet:
    x: float
    y: float

@dataclass
class Enemy:
    x: float
    y: float
    speed: float
    hits_remaining: int

# --- Game Logic ---
class AtlantisGameLogic:
    def __init__(self, config: AtlantisConfig):
        self.cfg = config
        self.reset()

    def reset(self):
        cfg = self.cfg
        # turret x positions equally spaced
        self.turret_x = [int((i+1)*(cfg.screen_width/(cfg.turret_count+1)))
                         for i in range(cfg.turret_count)]
        # tower positions at top
        self.tower_x = [int((i+1)*(cfg.screen_width/(cfg.tower_count+1)))
                        for i in range(cfg.tower_count)]
        self.bullets: List[Bullet] = []
        self.enemies: List[Enemy] = []
        self.towers_alive = [True]*cfg.tower_count
        self.fire_cooldown = 0
        self.spawn_timer = 0
        self.enemies_spawned = 0
        self.wave = 1
        self.score = 0
        self.game_over = False

    def update(self, action: GameAction):
        if self.game_over:
            return
        phys = self.cfg.physics
        # handle fire cooldown
        if self.fire_cooldown > 0:
            self.fire_cooldown -= 1
        # shoot
        if action in (GameAction.SHOOT_LEFT, GameAction.SHOOT_MIDDLE, GameAction.SHOOT_RIGHT):
            if self.fire_cooldown == 0:
                idx = action.value
                x = self.turret_x[idx]
                turret_radius = self.cfg.dimensions.TOWER_HEIGHT / 2
                bullet_radius = self.cfg.dimensions.BULLET_RADIUS
                y = self.cfg.turret_y - turret_radius - bullet_radius
                self.bullets.append(Bullet(x, y))
                self.fire_cooldown = phys.FIRE_COOLDOWN

        # spawn enemies
        self.spawn_timer += 1
        if (self.spawn_timer >= phys.ENEMY_SPAWN_INTERVAL and
            self.enemies_spawned < phys.WAVE_SIZE * self.wave):

            ex = 0
            ey = self.cfg.tower_y / 10 #Spawn enemies at the top of the screen.

            speed = phys.ENEMY_SPEED_BASE + (self.wave-1)*phys.ENEMY_SPEED_INC
            self.enemies.append(Enemy(ex, ey, speed, hits_remaining=1))
            self.spawn_timer = 0
            self.enemies_spawned += 1

        # update bullets
        for b in list(self.bullets):
            b.y -= phys.BULLET_SPEED
            if b.y < 0:
                self.bullets.remove(b)

        # update enemies
        for e in list(self.enemies):
            e.x += e.speed
            # collision with bullets
            hit = False
            for b in list(self.bullets):
                if abs(b.x - e.x) < self.cfg.dimensions.ENEMY_SIZE and abs(b.y - e.y) < self.cfg.dimensions.ENEMY_SIZE:
                    e.hits_remaining -= 1
                    self.bullets.remove(b)
                    if e.hits_remaining <= 0:
                        self.enemies.remove(e)
                        self.score += 100
                    hit = True
                    break
            if hit:
                continue


            # Currently just to showcase that the end of the screen is reached.
            # Need to update the logic with enemy bullet
            if e.x >= self.cfg.screen_width:
                # find nearest tower
                distances = [abs(e.x - tx) for tx in self.tower_x]

                idx = distances.index(min(distances))
                # This logic is not correct and needs replacing with the Bomb shells logic. TODO
                # if self.towers_alive[idx]:
                #     self.towers_alive[idx] = False
                self.enemies.remove(e)
                # check game over
                if not any(self.towers_alive):
                    self.game_over = True

        # wave complete
        if self.enemies_spawned >= phys.WAVE_SIZE * self.wave and not self.enemies:
            self.wave += 1
            self.enemies_spawned = 0
            # escalate difficulty by increasing wave size

# --- Rendering ---

class AtlantisRenderer:
    def __init__(self, logic: AtlantisGameLogic, render_cfg: RenderConfig):
        pygame.init()
        self.logic = logic
        self.cfg = logic.cfg
        self.rc = render_cfg
        self.screen = pygame.display.set_mode((self.cfg.screen_width, self.cfg.screen_height))
        pygame.display.set_caption("Atlantis II")
        self.font = pygame.font.Font(None, self.rc.font_size)

    def render(self):
        self.screen.fill(self.rc.colors["background"])

        # Draw towers

        # for tx, alive in zip(self.logic.tower_x, self.logic.towers_alive):
        #     color = self.rc.colors["tower"] if alive else (50,50,50)
        #
        #     rect = pygame.Rect(tx - self.cfg.dimensions.TOWER_WIDTH//2,
        #                        self.cfg.tower_y - self.cfg.dimensions.TOWER_HEIGHT//2,
        #                        self.cfg.dimensions.TOWER_WIDTH,
        #                        self.cfg.dimensions.TOWER_HEIGHT)
        #
        #     pygame.draw.rect(self.screen, color, rect)

        for tx, alive in zip(self.logic.tower_x, self.logic.towers_alive):
            # choose color
            color = self.rc.colors["tower"] if alive else (50, 50, 50)

            # build the tower rectangle
            rect = pygame.Rect(
                tx - self.cfg.dimensions.TOWER_WIDTH // 2,
                self.cfg.tower_y - self.cfg.dimensions.TOWER_HEIGHT // 2,
                self.cfg.dimensions.TOWER_WIDTH,
                self.cfg.dimensions.TOWER_HEIGHT
            )
            # draw the rectangle
            pygame.draw.rect(self.screen, color, rect)

            # compute triangle points
            top_center = (rect.centerx, rect.top - 10)  # apex 10px above the rect
            base_left = (rect.left, rect.top)  # left corner of the base
            base_right = (rect.right, rect.top)  # right corner of the base
            triangle_pts = [top_center, base_left, base_right]

            colortriangle = (255,140,0) if alive else (50,50,50)

            # draw the triangle
            pygame.draw.polygon(self.screen, colortriangle, triangle_pts)

        # draw turrets
        for x in self.logic.turret_x:
            pygame.draw.circle(self.screen, self.rc.colors["turret"], (x, self.cfg.turret_y), 10)
        # draw bullets
        for b in self.logic.bullets:
            pygame.draw.circle(self.screen, self.rc.colors["bullet"], (int(b.x), int(b.y)), self.cfg.dimensions.BULLET_RADIUS)
        # draw enemies
        for e in self.logic.enemies:
            pygame.draw.rect(self.screen, self.rc.colors["enemy"],
                             (e.x - self.cfg.dimensions.ENEMY_SIZE//2,
                              e.y - self.cfg.dimensions.ENEMY_SIZE//2,
                              self.cfg.dimensions.ENEMY_SIZE,
                              self.cfg.dimensions.ENEMY_SIZE))
        # HUD
        txt = f"Score: {self.logic.score}  Wave: {self.logic.wave}"
        surf = self.font.render(txt, True, self.rc.colors["text"])
        self.screen.blit(surf, (10, 10))
        # game over
        if self.logic.game_over:
            go = self.font.render("GAME OVER", True, self.rc.colors["text"])
            rect = go.get_rect(center=(self.cfg.screen_width//2, self.cfg.screen_height//2))
            self.screen.blit(go, rect)
        pygame.display.flip()

# --- Controller ---
class GameController:
    def __init__(self):
        self.config = AtlantisConfig()
        self.logic = AtlantisGameLogic(self.config)
        self.renderer = AtlantisRenderer(self.logic, RenderConfig())
        self.clock = pygame.time.Clock()

    def run(self):
        while True:
            action = GameAction.NOOP
            keys = pygame.key.get_pressed()
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.KEYDOWN and ev.key == pygame.K_SPACE:
                    if keys[pygame.K_LEFT]:
                        action = GameAction.SHOOT_LEFT
                    elif keys[pygame.K_RIGHT]:
                        action = GameAction.SHOOT_RIGHT
                    else:
                        action = GameAction.SHOOT_MIDDLE
            self.logic.update(action)
            self.renderer.render()
            self.clock.tick(self.config.fps)

# --- Main ---
def main():
    GameController().run()

if __name__ == "__main__":
    main()
import sys
import random
import math
from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Dict, Tuple

# --- Configuration ---
@dataclass(frozen=True)
class GameConfig:
    screen_width: int = 800
    screen_height: int = 600
    fps: int = 60
    center: Tuple[int, int] = (400, 300)
    city_radius: int = 100
    turret_rotate_speed_deg: float = 180.0  # degrees per second
    harpoon_speed: float = 8.0
    base_fire_cooldown: int = 15  # frames
    spawn_interval: int = 60      # frames per enemy spawn
    base_enemy_speed: float = 1.5
    enemy_speed_inc: float = 0.2
    enemies_per_level: int = 10
    spawn_radius: int = 400
    powerup_chance: float = 0.2
    pearl_speed: float = 3.0
    combo_window: int = 30        # frames
    max_city_health: int = 100
    max_oxygen: float = 100.0
    oxygen_depletion: float = 0.05 # per frame
    powerup_duration: int = 300    # frames

@dataclass(frozen=True)
class RenderConfig:
    colors: Dict[str, Tuple[int,int,int]] = field(default_factory=lambda: {
        'background': (0, 10, 30),
        'city': (0, 100, 200),
        'turret': (200, 200, 200),
        'harpoon': (255, 255, 0),
        'squid': (150, 0, 150),
        'crab': (200, 0, 0),
        'eel': (200, 200, 0),
        'pearl': (255, 255, 255),
        'ui': (255, 255, 255),
        'shield': (0, 255, 255),
        'pause': (0, 0, 0, 180)
    })
    font_name: str = None
    font_size: int = 20

# --- Enums ---
class GameAction(Enum):
    ROTATE_LEFT = auto()
    ROTATE_RIGHT = auto()
    FIRE = auto()
    NOOP = auto()

class PowerUpType(Enum):
    SONAR = auto()
    RAPID_FIRE = auto()
    SHIELD = auto()
    OXYGEN = auto()

# --- Entities ---
@dataclass
class Harpoon:
    x: float
    y: float
    dx: float
    dy: float

@dataclass
class Enemy:
    x: float
    y: float
    dx: float
    dy: float
    type: str
    speed: float
    health: int
    point: int

@dataclass
class Pearl:
    x: float
    y: float
    dx: float
    dy: float
    ptype: PowerUpType

# --- Game State ---
@dataclass
class GameState:
    turret_angle: float
    fire_cooldown: int
    harpoons: List[Harpoon]
    enemies: List[Enemy]
    pearls: List[Pearl]
    score: int
    combo_count: int
    last_kill_frame: int
    city_health: int
    oxygen: float
    lives: int
    level: int
    frames: int
    paused: bool
    upgrade_menu: bool
    active_powerups: Dict[PowerUpType, int]
    shield_active: bool
    game_over: bool

# --- Game Logic ---
class GameLogic:
    def __init__(self, gc: GameConfig):
        self.gc = gc
        self.reset()

    def reset(self):
        self.state = GameState(
            turret_angle=0.0,
            fire_cooldown=0,
            harpoons=[],
            enemies=[],
            pearls=[],
            score=0,
            combo_count=0,
            last_kill_frame=-999,
            city_health=self.gc.max_city_health,
            oxygen=self.gc.max_oxygen,
            lives=3,
            level=1,
            frames=0,
            paused=False,
            upgrade_menu=False,
            active_powerups={pu:0 for pu in PowerUpType},
            shield_active=False,
            game_over=False
        )
        self.spawned = 0
        self.killed = 0

    def step(self, action: GameAction):
        s = self.state
        # Pause toggle handled externally
        if s.paused or s.game_over or s.upgrade_menu:
            return

        # 1) Turret rotation
        rot_speed = math.radians(self.gc.turret_rotate_speed_deg) / self.gc.fps
        if action == GameAction.ROTATE_LEFT:
            s.turret_angle -= rot_speed
        elif action == GameAction.ROTATE_RIGHT:
            s.turret_angle += rot_speed
        s.turret_angle %= 2*math.pi

        # 2) Fire harpoon
        if s.fire_cooldown > 0:
            s.fire_cooldown -= 1
        if action == GameAction.FIRE and s.fire_cooldown == 0:
            cx, cy = self.gc.center
            angle = s.turret_angle
            x0 = cx + self.gc.city_radius * math.cos(angle)
            y0 = cy + self.gc.city_radius * math.sin(angle)
            dx = self.gc.harpoon_speed * math.cos(angle)
            dy = self.gc.harpoon_speed * math.sin(angle)
            s.harpoons.append(Harpoon(x0, y0, dx, dy))
            s.fire_cooldown = self.gc.base_fire_cooldown

        # 3) Spawn enemies
        if s.frames % self.gc.spawn_interval == 0 and self.spawned < self.gc.enemies_per_level:
            angle = random.random() * 2 * math.pi
            x0 = self.gc.center[0] + self.gc.spawn_radius * math.cos(angle)
            y0 = self.gc.center[1] + self.gc.spawn_radius * math.sin(angle)
            # choose type
            etype = random.choice(['squid', 'crab', 'eel'])
            speed = self.gc.base_enemy_speed + (s.level-1)*self.gc.enemy_speed_inc
            # simple health/point mapping
            hp = {'squid':2,'crab':3,'eel':1}[etype]
            pt = {'squid':150,'crab':200,'eel':100}[etype]
            # direction toward center
            dist = math.hypot(self.gc.center[0]-x0, self.gc.center[1]-y0)
            dx = (self.gc.center[0]-x0)/dist * speed
            dy = (self.gc.center[1]-y0)/dist * speed
            s.enemies.append(Enemy(x0,y0,dx,dy,etype,speed,hp,pt))
            self.spawned += 1

        # 4) Update harpoons
        for h in list(s.harpoons):
            h.x += h.dx
            h.y += h.dy
            # remove if out of bounds
            if (h.x<0 or h.x>self.gc.screen_width or
                h.y<0 or h.y>self.gc.screen_height):
                s.harpoons.remove(h)

        # 5) Update enemies
        for e in list(s.enemies):
            e.x += e.dx
            e.y += e.dy
            # reached city
            dist_c = math.hypot(e.x-self.gc.center[0], e.y-self.gc.center[1])
            if dist_c <= self.gc.city_radius:
                if not s.shield_active:
                    s.city_health -= 10
                s.enemies.remove(e)
                if s.city_health <= 0:
                    s.lives -= 1
                    s.city_health = self.gc.max_city_health
                    if s.lives <= 0:
                        s.game_over = True
                continue
            # collision with harpoon
            for h in list(s.harpoons):
                if math.hypot(e.x-h.x, e.y-h.y) < 10:
                    # hit
                    e.health -= 1
                    s.harpoons.remove(h)
                    if e.health <= 0:
                        s.enemies.remove(e)
                        # combo logic
                        if s.frames - s.last_kill_frame <= self.gc.combo_window:
                            s.combo_count += 1
                        else:
                            s.combo_count = 0
                        s.last_kill_frame = s.frames
                        pts = e.point * (1 + s.combo_count)
                        s.score += pts
                        # drop pearl?
                        if random.random() < self.gc.powerup_chance:
                            angle = math.atan2(e.y-self.gc.center[1], e.x-self.gc.center[0])
                            px, py = e.x, e.y
                            dx = math.cos(angle)*self.gc.pearl_speed
                            dy = math.sin(angle)*self.gc.pearl_speed
                            ptype = random.choice(list(PowerUpType))
                            s.pearls.append(Pearl(px,py,dx,dy,ptype))
                    break

        # 6) Update pearls
        for p in list(s.pearls):
            p.x += p.dx
            p.y += p.dy
            # collect if near turret tip
            cx,cy = self.gc.center
            tip_x = cx + self.gc.city_radius*math.cos(s.turret_angle)
            tip_y = cy + self.gc.city_radius*math.sin(s.turret_angle)
            if math.hypot(p.x-tip_x, p.y-tip_y) < 15:
                self.apply_powerup(p.ptype)
                s.pearls.remove(p)
            # remove out of bounds
            if (p.x<0 or p.x>self.gc.screen_width or
                p.y<0 or p.y>self.gc.screen_height):
                s.pearls.remove(p)

        # 7) Powerup durations
        for pu, t in s.active_powerups.items():
            if t>0:
                s.active_powerups[pu] -= 1
                if s.active_powerups[pu] == 0:
                    # expire
                    if pu == PowerUpType.RAPID_FIRE:
                        s.fire_cooldown = self.gc.base_fire_cooldown
                    if pu == PowerUpType.SHIELD:
                        s.shield_active = False

        # 8) Oxygen
        s.oxygen -= self.gc.oxygen_depletion
        if s.oxygen <= 0:
            s.lives -= 1
            s.oxygen = self.gc.max_oxygen
            if s.lives <= 0:
                s.game_over = True

        # 9) Level complete
        if self.spawned == self.gc.enemies_per_level and not s.enemies:
            s.upgrade_menu = True

        s.frames += 1

    def apply_powerup(self, pu: PowerUpType):
        s = self.state
        if pu == PowerUpType.SONAR:
            # clear nearby foes
            cleared = [e for e in s.enemies if math.hypot(e.x-self.gc.center[0], e.y-self.gc.center[1])<self.gc.city_radius*1.5]
            for e in cleared:
                s.enemies.remove(e)
                s.score += e.point
        elif pu == PowerUpType.RAPID_FIRE:
            s.fire_cooldown = max(1, self.gc.base_fire_cooldown//2)
            s.active_powerups[pu] = self.gc.powerup_duration
        elif pu == PowerUpType.SHIELD:
            s.shield_active = True
            s.active_powerups[pu] = self.gc.powerup_duration
        elif pu == PowerUpType.OXYGEN:
            s.oxygen = self.gc.max_oxygen
            s.active_powerups[pu] = 0

# --- Rendering ---
class Renderer:
    def __init__(self, gc: GameConfig, rc: RenderConfig):
        pygame.init()
        self.gc, self.rc = gc, rc
        self.screen = pygame.display.set_mode((gc.screen_width, gc.screen_height))
        pygame.display.set_caption("Atlantis 2")
        self.font = pygame.font.Font(rc.font_name, rc.font_size)

    def render(self, state: GameState):
        # background
        self.screen.fill(self.rc.colors['background'])
        cx, cy = self.gc.center
        # city dome
        pygame.draw.circle(self.screen, self.rc.colors['city'], (cx,cy), self.gc.city_radius)
        # harpoons
        for h in state.harpoons:
            pygame.draw.line(self.screen, self.rc.colors['harpoon'], (h.x,h.y), (h.x-h.dx,h.y-h.dy), 2)
        # enemies
        for e in state.enemies:
            col = self.rc.colors[e.type]
            pygame.draw.circle(self.screen, col, (int(e.x),int(e.y)), 12)
        # pearls
        for p in state.pearls:
            pygame.draw.circle(self.screen, self.rc.colors['pearl'], (int(p.x),int(p.y)), 6)
        # turret
        angle = state.turret_angle
        tip = (cx + self.gc.city_radius*math.cos(angle), cy + self.gc.city_radius*math.sin(angle))
        pygame.draw.line(self.screen, self.rc.colors['turret'], (cx,cy), tip, 4)
        pygame.draw.circle(self.screen, self.rc.colors['turret'], (cx,cy), 8)
        # HUD
        txt = f"Score:{state.score} Lives:{state.lives} Level:{state.level} Health:{state.city_health} O2:{int(state.oxygen)}"
        surf = self.font.render(txt, True, self.rc.colors['ui'])
        self.screen.blit(surf, (10,10))
        # pause / game over
        if state.paused:
            overlay = pygame.Surface((self.gc.screen_width,self.gc.screen_height), pygame.SRCALPHA)
            overlay.fill(self.rc.colors['pause'])
            self.screen.blit(overlay,(0,0))
            ps = self.font.render("PAUSED",True,self.rc.colors['ui'])
            r = ps.get_rect(center=(cx,cy))
            self.screen.blit(ps,r)
        if state.upgrade_menu:
            overlay = pygame.Surface((self.gc.screen_width,self.gc.screen_height), pygame.SRCALPHA)
            overlay.fill(self.rc.colors['pause'])
            self.screen.blit(overlay,(0,0))
            msg = "Upgrade! F:Fire Rate  S:Harpoon Speed  H:Hull"
            us = self.font.render(msg,True,self.rc.colors['ui'])
            r = us.get_rect(center=(cx,cy))
            self.screen.blit(us,r)
        if state.game_over:
            go = self.font.render("GAME OVER",True,self.rc.colors['ui'])
            r = go.get_rect(center=(cx,cy-20))
            self.screen.blit(go,r)
            fs = self.font.render(f"Final Score: {state.score}",True,self.rc.colors['ui'])
            r2 = fs.get_rect(center=(cx,cy+20))
            self.screen.blit(fs,r2)
        pygame.display.flip()

# --- Controller ---
class GameController:
    def __init__(self):
        self.gc = GameConfig()
        self.rc = RenderConfig()
        self.logic = GameLogic(self.gc)
        self.renderer = Renderer(self.gc, self.rc)
        self.clock = pygame.time.Clock()

    def run(self):
        while True:
            action = GameAction.NOOP
            for ev in pygame.event.get():
                if ev.type == pygame.QUIT:
                    pygame.quit(); sys.exit()
                if ev.type == pygame.KEYDOWN:
                    if self.logic.state.upgrade_menu:
                        if ev.key == pygame.K_f:
                            # speed up fire
                            self.gc = self.gc
                            self.logic.state.upgrade_menu = False
                            self.logic.reset()  # start next level
                        if ev.key == pygame.K_s:
                            # speed up harpoon
                            self.gc = GameConfig(harpoon_speed=self.gc.harpoon_speed*1.2)
                            self.logic.state.upgrade_menu = False
                            self.logic.reset()
                        if ev.key == pygame.K_h:
                            # increase hull
                            self.gc = GameConfig(max_city_health=self.gc.max_city_health+20)
                            self.logic.state.upgrade_menu = False
                            self.logic.reset()
                    else:
                        if ev.key in (pygame.K_a, pygame.K_LEFT):
                            action = GameAction.ROTATE_LEFT
                        elif ev.key in (pygame.K_d, pygame.K_RIGHT):
                            action = GameAction.ROTATE_RIGHT
                        elif ev.key == pygame.K_SPACE:
                            action = GameAction.FIRE
                        elif ev.key == pygame.K_p:
                            self.logic.state.paused = not self.logic.state.paused
                        elif ev.key == pygame.K_r and self.logic.state.game_over:
                            self.logic.reset()
            self.logic.step(action)
            self.renderer.render(self.logic.state)
            self.clock.tick(self.gc.fps)

# --- Main ---
def main():
    GameController().run()

if __name__ == "__main__":
    main()
