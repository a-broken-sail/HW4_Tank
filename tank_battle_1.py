# -*- coding: utf-8 -*-
"""Tank Battle —— 单文件 Pygame 示例。

在保持原有结构（常量区 -> 音频 -> 精灵类 -> 地图 -> TankGame）的前提下做了这些改进：
  1. 高 DPI 适配：进程级 DPI 感知 + 内部超采样画布 + 平滑缩放到窗口；
  2. UI 布局与视觉细节：顶部状态栏 / 底部操作栏 / 扁平化矢量贴图；
  3. 通行判定：缩小判定盒 + 拐角辅助 + 车道居中，不再需要像素级对齐；
  4. 音频：prelude.wav 开场、底噪循环、射击/命中音效全部即时构造（M 键静音）；
  5. 回车暂停 / 恢复（pause.wav），T 键切换手动 / 托管 AI；
  6. 地图：原版 26×26 半格布局（坦克占 2×2 格），砖墙最小单位是半格。
"""

import array
import ctypes
import heapq
import math
import os
import random
import sys

import pygame

# ===================== 常量 =====================
# 地图用原版数据，26×26 格 —— 这是“半坦克格”：原版一格 8px，一辆坦克占 2×2 格。
# 所以下面的 TILE 是半格，TANK 才是坦克；砖墙的最小单位也是半格，
# 这样“一格厚”的墙就真的只有半个车身厚（老鹰的砖罩正是这样）。
MAP_COLS = 26
MAP_ROWS = 26
TILE_LOGICAL = 20          # 半格，砖墙的最小单位
TANK_LOGICAL = TILE_LOGICAL * 2   # 坦克 2×2 格

HUD_HEIGHT = 64          # 顶部状态栏高度（逻辑像素）
FOOTER_HEIGHT = 40       # 底部操作提示栏高度（逻辑像素）
SIDE_PAD = 24            # 战场左右留白（逻辑像素）

# 逻辑分辨率：所有玩法坐标都按这个坐标系书写，跟着地图尺寸一起算出来
LOGICAL_WIDTH = MAP_COLS * TILE_LOGICAL + SIDE_PAD * 2
LOGICAL_HEIGHT = HUD_HEIGHT + MAP_ROWS * TILE_LOGICAL + FOOTER_HEIGHT

# 内部超采样倍率：先在 2 倍画布上用矢量方式绘制，再平滑缩放到窗口尺寸。
# 这样无论系统缩放倍率是多少，边缘都是抗锯齿的，不会出现大像素块。
SUPERSAMPLE = 2

SCREEN_WIDTH = LOGICAL_WIDTH * SUPERSAMPLE
SCREEN_HEIGHT = LOGICAL_HEIGHT * SUPERSAMPLE
TILE_SIZE = TILE_LOGICAL * SUPERSAMPLE       # 半格（砖墙单位）
TANK_SIZE = TANK_LOGICAL * SUPERSAMPLE       # 坦克贴图边长
BLOCK_SIZE = TANK_SIZE                       # AI 眼里的“一格”＝一个坦克位
FPS = 60


def px(value):
    """逻辑像素 -> 内部渲染像素。"""
    return int(round(value * SUPERSAMPLE))


def shade(color, factor):
    """按比例调暗 / 调亮颜色，用于扁平风格里的明暗层次。"""
    return tuple(max(0, min(255, int(round(channel * factor)))) for channel in color[:3])


BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (100, 100, 100)
GREEN = (0, 180, 0)
RED = (200, 50, 50)
BLUE = (50, 100, 200)
YELLOW = (220, 200, 50)
BROWN = (160, 100, 50)
ORANGE = (230, 140, 50)

# 界面中性色：保持扁平，不引入渐变 / 霓虹 / 光效
DARK = (16, 17, 20)
PANEL = (34, 36, 41)
PANEL_LIGHT = (54, 58, 65)
GRID_LINE = (29, 32, 37)
TEXT_MUTED = (140, 147, 158)

UP = (0, -1)
DOWN = (0, 1)
LEFT = (-1, 0)
RIGHT = (1, 0)
STAY = (0, 0)      # AI 专用：原地不动（定点开火 / 正在打墙），和“没主意”区分开

PLAYER = "player"
ENEMY = "enemy"
BULLET = "bullet"
BRICK = "brick"
STEEL = "steel"
BASE = "base"

# 战场区域（内部渲染坐标系）
PLAY_ORIGIN_X = SIDE_PAD * SUPERSAMPLE
PLAY_ORIGIN_Y = HUD_HEIGHT * SUPERSAMPLE
PLAY_RECT = pygame.Rect(PLAY_ORIGIN_X, PLAY_ORIGIN_Y,
                        MAP_COLS * TILE_SIZE, MAP_ROWS * TILE_SIZE)

# 通行判定参数：判定盒比外观每边小一点，把“必须精确对齐”的容错让出来
TANK_HITBOX_INSET = px(3)     # 每边收 3 逻辑像素，通道两侧共多出 6 像素容错
CORNER_ASSIST_RANGE = px(13)  # 拐角辅助：横向搜索通道口的最大距离
CORNER_ASSIST_STEP = px(3)    # 拐角辅助：每帧最多做的横向位移
LANE_PROBE = px(6)            # 车道居中：向两侧探测墙体存在的距离
LANE_CENTER_STEP = px(2)      # 车道居中：每帧向车道中线靠拢的最大位移

# 托管 AI 参数
AI_TURN_TOLERANCE = px(12)    # 离格子中心多近才允许拐弯
AI_SHOOT_TOLERANCE = px(10)   # 与目标对齐到这个精度就可以开火
AI_THREAT_RANGE = px(140)     # 判定“会打中我们”的子弹距离
AI_DODGE_CLEARANCE = px(24)   # 需要横向让开多少才算脱离弹道
AI_REPLAN_INTERVAL = 6        # 每隔多少帧重新规划一次路径
AI_HOLD_DISTANCE = px(80)     # 已经能打到敌人时，保持这个距离点射
AI_DODGE_FRAMES = 16          # 起躲之后锁定这个方向多少帧，避免又走回弹道
AI_PEEK_COOLDOWN = 8          # 冷却还剩这么多帧时先侧移让开射线
AI_PEEK_HOLD = 12             # 侧移方向锁定帧数，避免挪一步撞墙又弹回来
AI_PEEK_RANGE = px(160)       # 只有近处敌人才值得“侧移避枪”
AI_BREAK_RANGE = px(150)      # 子弹在这个距离内、侧面又让不开时，砸砖开路
AI_BRICK_COST = 5             # 寻路时穿过一个砖块格要付的代价（≈ 砸开它要花的帧数）
AI_BREAK_HOLD = 90            # 决定砸墙后锁定多少帧，打不通不换目标
AI_TARGET_STICKY = 2          # 换目标要等新目标比旧目标近这么多格，避免来回改主意

_FONT_CACHE = {}


def get_font(size):
    """按字号缓存字体对象，避免每块墙体重复构造字体。"""
    if size not in _FONT_CACHE:
        _FONT_CACHE[size] = pygame.font.Font(None, size)
    return _FONT_CACHE[size]


def detect_ui_scale():
    """开启进程级 DPI 感知，并返回显示器缩放倍率（1.0 起）。

    不做 DPI 感知时，Windows 会把窗口当位图整体拉伸，于是出现大像素块；
    开启之后窗口拿到真实物理像素，再由我们自己决定渲染分辨率。
    """
    if sys.platform != "win32":
        return 1.0
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # 逐显示器 DPI 感知
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()    # 老系统退路
        except Exception:
            pass
    try:
        hdc = ctypes.windll.user32.GetDC(0)
        dpi = ctypes.windll.gdi32.GetDeviceCaps(hdc, 88)  # LOGPIXELSX
        ctypes.windll.user32.ReleaseDC(0, hdc)
        return max(1.0, min(dpi / 96.0, 3.0))
    except Exception:
        return 1.0


def compute_window_size(scale):
    """逻辑尺寸 * 缩放倍率，并保证窗口不会超出桌面可用区域。"""
    try:
        desktop_w, desktop_h = pygame.display.get_desktop_sizes()[0]
    except Exception:
        desktop_w, desktop_h = 1920, 1080
    fit = min(desktop_w * 0.94 / LOGICAL_WIDTH, desktop_h * 0.92 / LOGICAL_HEIGHT)
    scale = max(0.75, min(scale, fit))
    return (max(320, int(round(LOGICAL_WIDTH * scale))),
            max(240, int(round(LOGICAL_HEIGHT * scale))))


# ===================== 音频 =====================
# prelude.wav / pause.wav 由外部提供；底噪与音效全部即时构造，不依赖素材文件。
SAMPLE_RATE = 44100
MUSIC_VOLUME = 0.55
AMBIENCE_VOLUME = 0.28
UI_VOLUME = 0.70
SHOT_VOLUME = 0.45
HIT_VOLUME = 0.55

# 底噪参数：对着参考录音拟合出来的（约 60Hz 的衰减脉冲串，不是白噪声）
AMBIENCE_PULSE_HZ = 60        # 静止时的脉冲频率，听感上是“嗡嗡”的音高
AMBIENCE_MOVE_PULSE_HZ = 88   # 开动时的脉冲频率（原版实测 88.4Hz）
AMBIENCE_DECAY = 0.9985       # 每采样衰减，决定每个脉冲拖多长的尾巴
AMBIENCE_SPREAD = 0.03        # 脉冲间隔的随机抖动，太大会把音高糊掉
AMBIENCE_DC = 0.993           # 隔直系数：压掉随机幅度带来的低频漂移，否则基频被 30Hz 晃动盖住
AMBIENCE_POSITIVE = 0.62      # 正向脉冲占比，越低波形越毛躁
AMBIENCE_FADE_MS = 220        # 静止 / 移动两套底噪交叉淡化的时长
AUDIO_DIR = os.path.dirname(os.path.abspath(__file__))


def load_sound(name):
    """从脚本所在目录读取 wav；文件缺失或损坏时返回 None。"""
    path = os.path.join(AUDIO_DIR, name)
    if not os.path.isfile(path):
        return None
    try:
        return pygame.mixer.Sound(path)
    except (pygame.error, OSError):
        return None


def _to_sound(samples, peak=0.9):
    """浮点样本 -> 16 位立体声 Sound（左右声道相同）。"""
    scale = peak / max(0.0001, max(max(samples), -min(samples)))
    data = array.array("h")
    for value in samples:
        sample = int(value * scale * 32767)
        data.append(sample)
        data.append(sample)
    return pygame.mixer.Sound(buffer=data.tobytes())


def build_ambience(pulse_hz=AMBIENCE_PULSE_HZ, seconds=4.0, seed=20261115):
    """老游戏底噪：约 60Hz 的衰减脉冲串。

    对着参考录音量出来的：这不是白噪声，而是一串周期性触发的“脉冲 + 指数衰减”，
    单个脉冲约 8ms 衰减掉，间隔随机抖动。听感是低沉的“嗡嗡”里混着沙沙的碎裂声。
    坦克开动时原版会换成一路更高的脉冲（88Hz 左右），所以这里把频率做成参数。
    """
    rng = random.Random(seed)              # 独立随机源，不干扰玩法里的随机数
    total = int(seconds * SAMPLE_RATE)
    buffer = []
    value = 0.0
    previous_in = 0.0
    previous_out = 0.0
    index = 0
    while index < total:
        amplitude = rng.uniform(0.4, 1.0)
        value += amplitude if rng.random() < AMBIENCE_POSITIVE else -amplitude
        step = int(SAMPLE_RATE / pulse_hz * (1 + rng.uniform(-AMBIENCE_SPREAD, AMBIENCE_SPREAD)))
        for _ in range(max(1, step)):
            if index >= total:
                break
            value *= AMBIENCE_DECAY
            # 隔直高通：脉冲幅度逐次随机会让波形整体缓慢漂移，
            # 不压掉的话 30Hz 的晃动会盖过 60Hz 的基频，听起来就没有音高了。
            output = value - previous_in + AMBIENCE_DC * previous_out
            previous_in, previous_out = value, output
            buffer.append(output)
            index += 1

    # 在末尾一小段里挑一个接近零的位置切断，循环接缝就不会“咔”一下
    peak = max(max(buffer), -min(buffer)) or 1.0
    end = len(buffer)
    for cursor in range(len(buffer) - 1, len(buffer) - 2000, -1):
        if abs(buffer[cursor]) < peak * 0.02:
            end = cursor + 1
            break
    return _to_sound(buffer[:end], peak=0.7)


def build_shot_sound():
    """发射：快速下滑的正弦 + 一点噪声，短促、不刺耳。"""
    rng = random.Random(4051)
    length = int(0.10 * SAMPLE_RATE)
    buffer = []
    phase = 0.0
    for index in range(length):
        moment = index / SAMPLE_RATE
        frequency = 520 * math.exp(-moment * 26) + 150
        phase += 2 * math.pi * frequency / SAMPLE_RATE
        tone = math.sin(phase) + 0.35 * math.sin(phase * 2)
        buffer.append((tone + rng.uniform(-0.35, 0.35)) * math.exp(-moment * 42))
    return _to_sound(buffer, peak=0.62)


def build_hit_sound():
    """命中：低频闷响 + 噪声炸开，带一点余震。"""
    rng = random.Random(90210)
    length = int(0.34 * SAMPLE_RATE)
    buffer = []
    phase = 0.0
    low = 0.0
    for index in range(length):
        moment = index / SAMPLE_RATE
        white = rng.uniform(-1.0, 1.0)
        low += (white - low) * 0.35
        phase += 2 * math.pi * (110 * math.exp(-moment * 16) + 48) / SAMPLE_RATE
        thump = math.sin(phase) * math.exp(-moment * 11)
        buffer.append((low * 0.9 + thump * 0.8) * math.exp(-moment * 7))
    return _to_sound(buffer, peak=0.85)


# ===================== 精灵类 =====================

class Tank(pygame.sprite.Sprite):
    def __init__(self, x, y, color, tank_type=ENEMY):
        super().__init__()
        # 坦克是 2×2 个半格，贴图按 40 逻辑像素画，下面的 px() 常数都是这个尺寸
        self.image = pygame.Surface((TANK_SIZE, TANK_SIZE))
        self.image.set_colorkey(BLACK)
        self.color = color
        self.tank_type = tank_type
        self.direction = DOWN
        self.speed = px(2)
        self.hp = 1
        self.cooldown = 0
        self.hitbox_inset = TANK_HITBOX_INSET
        self.draw_tank()
        self.rect = self.image.get_rect(topleft=(x, y))

    def draw_tank(self):
        self.image.fill(BLACK)
        self.image.set_colorkey(BLACK)

        size = TANK_SIZE
        frame = pygame.Rect(0, 0, size, size)
        dark = shade(self.color, 0.58)
        light = shade(self.color, 1.35)

        # 车体：深色描边 + 主色块，纯色填充，保持扁平
        pygame.draw.rect(self.image, dark, frame.inflate(-px(2), -px(2)), border_radius=px(9))
        pygame.draw.rect(self.image, self.color, frame.inflate(-px(9), -px(9)), border_radius=px(6))

        # 履带：沿运动方向的两条深色块，顺便指示朝向
        if self.direction in (UP, DOWN):
            for tread_x in (px(5), size - px(5) - px(5)):
                pygame.draw.rect(self.image, dark,
                                 (tread_x, px(12), px(5), size - px(24)), border_radius=px(2))
        else:
            for tread_y in (px(5), size - px(5) - px(5)):
                pygame.draw.rect(self.image, dark,
                                 (px(12), tread_y, size - px(24), px(5)), border_radius=px(2))

        cx, cy = size // 2, size // 2

        # 炮管：短而粗，从车体边缘探出一小截，方向一眼能看懂
        dx, dy = self.direction
        if dy:
            barrel = pygame.Rect(0, 0, px(10), px(26))
            barrel.center = (cx, cy + dy * px(23))
        else:
            barrel = pygame.Rect(0, 0, px(26), px(10))
            barrel.center = (cx + dx * px(23), cy)
        pygame.draw.rect(self.image, light, barrel, border_radius=px(3))

        # 炮塔：压在炮管根部
        pygame.draw.circle(self.image, light, (cx, cy), px(8))

    def collision_rect(self, body=None):
        """实际参与碰撞的判定盒（比外观略小）。"""
        body = self.rect if body is None else body
        return body.inflate(-2 * self.hitbox_inset, -2 * self.hitbox_inset)

    def check_collision(self, walls, tanks, body_rect=None):
        probe = self.collision_rect(body_rect)
        for wall in walls:
            if probe.colliderect(wall.rect):
                return True
        for tank in tanks:
            if tank is self or not isinstance(tank, Tank):
                continue
            if probe.colliderect(tank.rect):
                return True
        return False

    def lane_center(self, walls, dy):
        """如果左右（或上下）都是墙，说明正卡在恰好一个车身宽的通道里。

        返回车道正中对应的车身坐标，不在这种通道里则返回 None。
        """
        near = self.rect.move(-LANE_PROBE, 0) if dy else self.rect.move(0, -LANE_PROBE)
        far = self.rect.move(LANE_PROBE, 0) if dy else self.rect.move(0, LANE_PROBE)

        # 一次遍历同时收集两侧最近的墙边
        near_edge = far_edge = None
        for wall in walls:
            if wall.rect.colliderect(near):
                edge = wall.rect.right if dy else wall.rect.bottom
                near_edge = edge if near_edge is None else max(near_edge, edge)
            if wall.rect.colliderect(far):
                edge = wall.rect.left if dy else wall.rect.top
                far_edge = edge if far_edge is None else min(far_edge, edge)
            if near_edge is not None and far_edge is not None and far_edge - near_edge > TANK_SIZE:
                return None  # 已经比一个车身宽，提前结束

        # 只有宽度恰好约等于车身的缝才做居中，宽广场地不干涉玩家走位
        if near_edge is None or far_edge is None:
            return None
        if abs(far_edge - near_edge - TANK_SIZE) > px(1):
            return None
        return (near_edge + far_edge - TANK_SIZE) // 2

    def align_to_lane(self, walls, tanks, dy):
        """在一格宽的通道里把车身轻推到正中，避免贴着一边蹭过去。"""
        target = self.lane_center(walls, dy)
        if target is None:
            return
        delta = target - (self.rect.x if dy else self.rect.y)
        if not delta:
            return
        step = max(-LANE_CENTER_STEP, min(LANE_CENTER_STEP, delta))
        probe = self.rect.move(step, 0) if dy else self.rect.move(0, step)
        if not self.check_collision(walls, tanks, probe):
            self.rect = probe

    def find_opening_offset(self, dx, dy, walls, tanks):
        """轴向受阻时，找最近的、能让坦克继续前进的横向偏移量。

        返回带符号的偏移（0 表示四周都被堵死），调用方再按每帧步长逼近它。
        """
        for offset in range(1, CORNER_ASSIST_RANGE + 1):
            for sign in (1, -1):
                if dy:
                    probe = self.rect.move(sign * offset, dy)
                else:
                    probe = self.rect.move(dx, sign * offset)
                if not self.check_collision(walls, tanks, probe):
                    return sign * offset
        return 0

    def move(self, dx, dy, walls, tanks):
        """带拐角辅助的移动。

        前进被挡住时不再原地卡住，而是先把车身往最近的通道口挪一小段，
        对齐之后自动继续前进；真正钻进一格宽的通道后，再把车身扶到正中，
        因此不必再靠像素级微调去“对准缝隙”。
        """
        if not dx and not dy:
            return False

        start = self.rect.copy()

        # 1) 直接前进
        self.rect = start.move(dx, dy)
        self.rect.clamp_ip(PLAY_RECT)
        if not self.check_collision(walls, tanks):
            self.align_to_lane(walls, tanks, dy)
            return True

        # 2) 拐角辅助：向最近的通道口横向微调
        offset = self.find_opening_offset(dx, dy, walls, tanks)
        if offset == 0:
            self.rect = start
            return False

        step = max(-CORNER_ASSIST_STEP, min(CORNER_ASSIST_STEP, offset))
        self.rect = start.move(step, 0) if dy else start.move(0, step)
        self.rect.clamp_ip(PLAY_RECT)
        if self.check_collision(walls, tanks):
            self.rect = start
            return False

        # 横向已经对齐的话，这一帧顺带把前进也补上，手感更连贯
        advance = self.rect.move(dx, dy)
        if not self.check_collision(walls, tanks, advance):
            self.rect = advance
            self.rect.clamp_ip(PLAY_RECT)
        self.align_to_lane(walls, tanks, dy)
        return True

    def set_direction(self, direction):
        """朝向变化时才重绘贴图，避免每帧重建 Surface。"""
        if direction != self.direction:
            self.direction = direction
            self.draw_tank()

    def can_shoot(self):
        if self.cooldown > 0:
            return False
        self.cooldown = 30
        return True

    def update(self, walls, tanks):
        if self.cooldown > 0:
            self.cooldown -= 1


class Bullet(pygame.sprite.Sprite):
    def __init__(self, x, y, direction, owner):
        super().__init__()
        size = px(8)
        self.image = pygame.Surface((size, size))
        self.image.set_colorkey(BLACK)
        center = (size // 2, size // 2)
        pygame.draw.circle(self.image, shade(YELLOW, 0.62), center, size // 2)
        pygame.draw.circle(self.image, YELLOW, center, max(1, size // 2 - px(2)))
        self.rect = self.image.get_rect(center=(x, y))
        self.direction = direction
        self.speed = px(6)
        self.owner = owner

    def update(self):
        dx, dy = self.direction
        self.rect.x += dx * self.speed
        self.rect.y += dy * self.speed


class Wall(pygame.sprite.Sprite):
    def __init__(self, x, y, wall_type, tiles=(1, 1)):
        super().__init__()
        self.wall_type = wall_type
        # 老鹰占 2×2 个半格（正好一个坦克位），用一整块贴图，
        # 免得四块并排各画一遍边框和文字
        self.image = pygame.Surface((TILE_SIZE * tiles[0], TILE_SIZE * tiles[1]))
        self.image.set_colorkey(BLACK)
        rect = self.image.get_rect()
        if wall_type == BRICK:
            self.image.fill(BROWN)
            mortar = shade(BROWN, 0.35)
            # 每个半格里画 2×2 块小砖，正好对上原版 8px 砖块的密度
            brick_w, brick_h = TILE_SIZE // 2, TILE_SIZE // 2
            for row in range(0, rect.bottom, brick_h):
                pygame.draw.line(self.image, mortar, (0, row), (rect.right, row), px(2))
                offset = 0 if (row // brick_h) % 2 == 0 else brick_w // 2
                for col in range(offset, rect.right + brick_w, brick_w):
                    pygame.draw.line(self.image, mortar, (col, row), (col, row + brick_h), px(2))
        elif wall_type == STEEL:
            self.image.fill(GRAY)
            pygame.draw.rect(self.image, shade(GRAY, 0.55), rect, px(1))
            pygame.draw.rect(self.image, shade(GRAY, 1.30),
                             rect.inflate(-px(7), -px(7)), px(2), border_radius=px(3))
            rivet = shade(GRAY, 1.45)
            for rivet_x in (px(5), rect.right - px(5)):
                for rivet_y in (px(5), rect.bottom - px(5)):
                    pygame.draw.circle(self.image, rivet, (rivet_x, rivet_y), px(2))
        elif wall_type == BASE:
            self.image.fill(ORANGE)
            pygame.draw.rect(self.image, shade(ORANGE, 0.55), rect, px(2))
            pygame.draw.rect(self.image, shade(ORANGE, 1.25),
                             rect.inflate(-px(8), -px(8)), px(2), border_radius=px(4))
            text = get_font(px(11)).render("BASE", True, shade(ORANGE, 0.28))
            self.image.blit(text, text.get_rect(center=rect.center))
        self.rect = self.image.get_rect(topleft=(x, y))


# ===================== 地图 =====================
# 原版关卡数据，26×26 个“半格”。图例：
#   .  空地      B  砖墙（可打穿）      S  钢墙（不可破坏）      A  老鹰
#
# 注意这里的一格是**半个坦克**：坦克、老鹰都占 2×2 格。所以下面这些
# 2 格宽、2 格高的砖柱，在游戏里正好是一个车身宽；老鹰那 2×2 的 A 也是
# 一个车身大，四周只有 1 格（半个车身）厚的砖罩 —— 和原版一模一样。
MAP_LAYOUT = [
    "..........................",
    "..........................",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..BBSSBB..BB..BB..",
    "..BB..BB..BBSSBB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..........BB..BB..",
    "..BB..BB..........BB..BB..",
    "..........BB..BB..........",
    "..........BB..BB..........",
    "BB..BBBB..........BBBB..BB",
    "SS..BBBB..........BBBB..SS",
    "..........BB..BB..........",
    "..........BBBBBB..........",
    "..BB..BB..BBBBBB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..BB..BB..BB..BB..",
    "..BB..BB..........BB..BB..",
    "..BB..BB..........BB..BB..",
    "..BB..BB...BBBB...BB..BB..",
    "...........BAAB...........",
    "...........BAAB...........",
]


def create_map():
    walls = pygame.sprite.Group()
    base = None
    rows = len(MAP_LAYOUT)
    cols = len(MAP_LAYOUT[0])
    # 地图居中放在战场区域内
    offset_x = PLAY_ORIGIN_X + (MAP_COLS - cols) * TILE_SIZE // 2
    offset_y = PLAY_ORIGIN_Y + (MAP_ROWS - rows) * TILE_SIZE // 2

    base_cells = []
    for row_idx, row in enumerate(MAP_LAYOUT):
        for col_idx, ch in enumerate(row):
            x = offset_x + col_idx * TILE_SIZE
            y = offset_y + row_idx * TILE_SIZE
            if ch == "B":
                walls.add(Wall(x, y, BRICK))
            elif ch == "S":
                walls.add(Wall(x, y, STEEL))
            elif ch == "A":
                base_cells.append((x, y))

    # 老鹰是 2×2 个半格，合并成一块贴图（正好一个车身），
    # 否则四块 BASE 并排会把边框和文字各画一遍
    if base_cells:
        base_cells.sort()
        left = min(cell[0] for cell in base_cells)
        top = min(cell[1] for cell in base_cells)
        wide = (max(cell[0] for cell in base_cells) - left) // TILE_SIZE + 1
        tall = (max(cell[1] for cell in base_cells) - top) // TILE_SIZE + 1
        base = Wall(left, top, BASE, tiles=(wide, tall))
        walls.add(base)

    return walls, base, offset_x, offset_y


# ===================== 游戏主类 =====================

class TankGame:
    def __init__(self):
        # DPI 感知必须在创建窗口之前打开
        self.ui_scale = detect_ui_scale()
        # 混音格式也要在 pygame.init() 之前定好，合成出来的样本才能直接用
        pygame.mixer.pre_init(SAMPLE_RATE, -16, 2, 512)
        pygame.init()
        self.window_size = compute_window_size(self.ui_scale)
        self.screen = pygame.display.set_mode(self.window_size, pygame.DOUBLEBUF)
        pygame.display.set_caption("Tank Battle - Pygame")
        # 游戏始终画在这张超采样画布上，最后一步才缩放到窗口
        self.canvas = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT)).convert()
        self.clock = pygame.time.Clock()
        self.running = True
        self.font_small = pygame.font.Font(None, px(14))
        self.font = pygame.font.Font(None, px(18))
        self.font_title = pygame.font.Font(None, px(36))
        pygame.key.stop_text_input()
        self.auto_pilot = False
        self.paused = False
        self.setup_audio()
        self.reset()

    def reset(self):
        self.all_sprites = pygame.sprite.Group()
        self.walls, self.base, self.offset_x, self.offset_y = create_map()
        self.bullets = pygame.sprite.Group()
        self.enemies = pygame.sprite.Group()
        # 玩家在老鹰左侧一个车身位，和原版一样从基地旁边出发
        self.player = Tank(self.offset_x + BLOCK_SIZE * 4,
                           self.offset_y + BLOCK_SIZE * 12,
                           GREEN, PLAYER)
        self.all_sprites.add(self.player)

        # 敌人从原版那三个出生点（左 / 中 / 右）压下来。
        # 注意别再往“和玩家同一条通道”的位置加人 —— 老鹰区那条竖巷是贯通的，
        # 出生点正上方多一个敌人，开局就会被隔着整张地图点名。
        enemy_positions = [
            (self.offset_x + BLOCK_SIZE * 0, self.offset_y + BLOCK_SIZE * 0),
            (self.offset_x + BLOCK_SIZE * 6, self.offset_y + BLOCK_SIZE * 0),
            (self.offset_x + BLOCK_SIZE * 12, self.offset_y + BLOCK_SIZE * 0),
        ]
        for pos in enemy_positions:
            enemy = Tank(pos[0], pos[1], RED, ENEMY)
            self.enemies.add(enemy)
            self.all_sprites.add(enemy)

        self.max_hp = self.player.hp
        self.total_enemies = len(self.enemies)
        self.game_over = False
        self.win = False
        # 托管 AI 的运行时状态
        self._cell_walls = None
        self._wall_count = -1
        self.ai_path = None
        self.ai_heading = None
        self.ai_replan = 0
        self.ai_dodge = 0
        self.ai_dodge_dir = None
        self.ai_target = None       # 当前锁定的目标敌人，避免来回换目标
        self.ai_break_hold = 0      # 砸墙锁定的剩余帧数
        self.ai_break_dir = None
        self.ai_peek_dir = None     # 侧移避枪的锁定方向
        self.ai_peek_hold = 0
        self.reset_music()

    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                if event.key == pygame.K_m:
                    self.toggle_audio()
                if event.key == pygame.K_t:
                    self.toggle_auto_pilot()
                if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    self.toggle_pause()
                if event.key == pygame.K_r and self.game_over:
                    self.reset()
                if event.key == pygame.K_SPACE and not self.game_over and not self.paused:
                    if self.player.can_shoot():
                        self.shoot(self.player)
            if event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1 and not self.game_over and not self.paused:
                    if self.player.can_shoot():
                        self.shoot(self.player)

    def setup_audio(self):
        self.prelude = None
        self.pause_sound = None
        self.ambience = None
        self.ambience_move = None
        self.shot_sound = None
        self.hit_sound = None
        self.music_channel = None
        self.ui_channel = None
        self.ambience_channels = ()
        self.ambience_index = 0
        self.ambience_moving = None
        self.audio_muted = False
        self.music_pending = False
        self.music_resume_pending = False
        if pygame.mixer.get_init() is None:
            return

        self.prelude = load_sound("prelude.wav")
        self.pause_sound = load_sound("pause.wav")
        if pygame.mixer.get_init() == (SAMPLE_RATE, -16, 2):
            try:
                self.ambience = build_ambience(AMBIENCE_PULSE_HZ)
                self.ambience_move = build_ambience(AMBIENCE_MOVE_PULSE_HZ, seed=771)
                self.shot_sound = build_shot_sound()
                self.hit_sound = build_hit_sound()
            except (pygame.error, ValueError):
                pass
        try:
            pygame.mixer.set_num_channels(16)
            pygame.mixer.set_reserved(4)
            self.music_channel = pygame.mixer.Channel(0)
            self.ui_channel = pygame.mixer.Channel(1)
            self.ambience_channels = (pygame.mixer.Channel(2), pygame.mixer.Channel(3))
        except pygame.error:
            self.music_channel = None
            self.ui_channel = None
            self.ambience_channels = ()

    def play_sfx(self, sound, volume=1.0):
        if sound is None or self.audio_muted:
            return
        sound.set_volume(max(0.0, min(1.0, volume)))
        sound.play()

    def play_ui(self, sound):
        """界面音走独立声道，一次只响一个。"""
        if sound is None or self.ui_channel is None:
            return
        self.ui_channel.stop()
        self.ui_channel.set_volume(UI_VOLUME)
        self.ui_channel.play(sound)

    def music_channels(self):
        """所有会随暂停一起停的声道。"""
        channels = [self.music_channel, *self.ambience_channels]
        return [channel for channel in channels if channel is not None]

    def set_ambience(self, moving, force=False):
        """切换静止 / 移动两套底噪，用两声道做交叉淡化。

        坦克一动就切到高八度那套（原版实测 61.9Hz → 88.4Hz），停住再切回来。
        """
        if moving == self.ambience_moving and not force:
            return
        sound = self.ambience_move if moving else self.ambience
        if sound is None or not self.ambience_channels:
            self.ambience_moving = moving
            return

        current = self.ambience_channels[self.ambience_index]
        following = self.ambience_channels[1 - self.ambience_index]
        current.fadeout(AMBIENCE_FADE_MS)
        following.set_volume(AMBIENCE_VOLUME)
        following.play(sound, loops=-1, fade_ms=AMBIENCE_FADE_MS)
        self.ambience_index = 1 - self.ambience_index
        self.ambience_moving = moving

    def reset_music(self):
        """开局先放 prelude.wav，放完自动接底噪。"""
        self.paused = False
        self.music_resume_pending = False
        self.ambience_moving = None
        if self.music_channel is None:
            self.music_pending = False
            return
        pygame.mixer.stop()                 # 先掐掉上一局残留的音效，别和开场曲叠在一起
        if self.prelude is not None:
            self.music_channel.play(self.prelude)
            self.music_channel.set_volume(MUSIC_VOLUME)
            self.music_pending = True
        else:
            self.music_pending = False
            self.set_ambience(False, force=True)

    def toggle_pause(self):
        """回车暂停 / 恢复。

        声音严格串行：暂停时音乐立刻停、只响 pause；恢复时先响完 pause，
        再把之前没放完的音乐接上，两条声音不会叠在一起。
        """
        if self.game_over:
            return
        self.paused = not self.paused
        self.play_ui(self.pause_sound)
        if not self.music_channels():
            return
        if self.paused:
            for channel in self.music_channels():
                channel.pause()
            self.music_resume_pending = False
        elif self.pause_sound is None:
            for channel in self.music_channels():
                channel.unpause()
            self.music_resume_pending = False
        else:
            self.music_resume_pending = True

    def toggle_audio(self):
        """M 键总静音。"""
        if pygame.mixer.get_init() is None:
            return
        self.audio_muted = not self.audio_muted
        if self.audio_muted:
            pygame.mixer.pause()
        else:
            pygame.mixer.unpause()
            if self.paused:
                for channel in self.music_channels():
                    channel.pause()   # 暂停状态下不能因为取消静音把音乐放出来

    def toggle_auto_pilot(self):
        """手动 / 托管之间切换，切换时清掉上一套决策状态。"""
        self.auto_pilot = not self.auto_pilot
        self.ai_path = None
        self.ai_replan = 0

    def shoot(self, tank):
        cx = tank.rect.centerx
        cy = tank.rect.centery
        dx, dy = tank.direction
        bullet = Bullet(cx + dx * (TANK_SIZE // 2), cy + dy * (TANK_SIZE // 2),
                        tank.direction, tank.tank_type)
        self.bullets.add(bullet)
        self.all_sprites.add(bullet)
        self.play_sfx(self.shot_sound, SHOT_VOLUME if tank.tank_type == PLAYER else SHOT_VOLUME * 0.45)

    def update(self):
        if self.game_over or self.paused:
            return

        # 恢复时先让 pause 响完，再把没放完的音乐接上
        if self.music_channel is not None:
            if self.music_resume_pending and not self.ui_channel.get_busy():
                self.music_resume_pending = False
                for channel in self.music_channels():
                    channel.unpause()
            if (self.music_pending and not self.music_resume_pending
                    and not self.music_channel.get_busy()):
                self.music_pending = False      # 前奏放完了，接上底噪
                self.set_ambience(False, force=True)

        position = self.player.rect.topleft
        if self.auto_pilot:
            self.auto_play()
        else:
            keys = pygame.key.get_pressed()
            if keys[pygame.K_w] or keys[pygame.K_UP] or keys[pygame.K_k]:
                self.player.set_direction(UP)
                self.player.move(0, -self.player.speed, self.walls, self.all_sprites)
            elif keys[pygame.K_s] or keys[pygame.K_DOWN] or keys[pygame.K_j]:
                self.player.set_direction(DOWN)
                self.player.move(0, self.player.speed, self.walls, self.all_sprites)
            elif keys[pygame.K_a] or keys[pygame.K_LEFT] or keys[pygame.K_h]:
                self.player.set_direction(LEFT)
                self.player.move(-self.player.speed, 0, self.walls, self.all_sprites)
            elif keys[pygame.K_d] or keys[pygame.K_RIGHT] or keys[pygame.K_l]:
                self.player.set_direction(RIGHT)
                self.player.move(self.player.speed, 0, self.walls, self.all_sprites)

        self.player.update(self.walls, self.all_sprites)

        # 坦克一开动就把底噪换成高音那套，停下来再换回去
        if not self.music_pending:
            self.set_ambience(self.player.rect.topleft != position)

        for enemy in self.enemies:
            self.ai_move(enemy)
            enemy.update(self.walls, self.all_sprites)
            if random.random() < 0.03 and enemy.can_shoot():
                self.shoot(enemy)

        self.bullets.update()

        for bullet in self.bullets:
            if not PLAY_RECT.colliderect(bullet.rect):
                bullet.kill()
                continue

            hit_walls = pygame.sprite.spritecollide(bullet, self.walls, False)
            if hit_walls:
                for wall in hit_walls:
                    if wall.wall_type == BRICK:
                        wall.kill()
##                    elif wall.wall_type == BASE:     #我不喜欢这个base，所以我把它comment out。
##                        wall.kill()                              #否则不小心打到Base，我就输了。
##                        self.game_over = True
##                        self.win = False
                bullet.kill()
                continue

            if bullet.owner == PLAYER:
                hit_enemies = pygame.sprite.spritecollide(bullet, self.enemies, False)
                for enemy in hit_enemies:
                    enemy.hp -= 1
                    self.play_sfx(self.hit_sound, HIT_VOLUME)
                    if enemy.hp <= 0:
                        enemy.kill()
                    bullet.kill()

            elif bullet.owner == ENEMY:
                if bullet.rect.colliderect(self.player.rect):
                    self.player.hp -= 1
                    self.play_sfx(self.hit_sound, HIT_VOLUME)
                    if self.player.hp <= 0:
                        self.player.kill()
                        self.game_over = True
                        self.win = False
                    bullet.kill()

        if not self.enemies and not self.game_over:
            self.game_over = True
            self.win = True

    def ai_move(self, enemy):
        dx, dy = enemy.direction
        success = enemy.move(dx * enemy.speed, dy * enemy.speed, self.walls, self.all_sprites)
        if not success or random.random() < 0.02:
            enemy.set_direction(random.choice([UP, DOWN, LEFT, RIGHT]))

    # ---------- 托管 AI ----------
    # 每帧的优先级：开火（不占用移动）> 躲弹 > 侧移避枪 > 沿 BFS 路径逼近最近的敌人。

    def cell_of(self, rect):
        """矩形左上角所在的坦克格坐标（一格 = 一个车身 = 2×2 个半格）。"""
        return (round((rect.x - self.offset_x) / BLOCK_SIZE),
                round((rect.y - self.offset_y) / BLOCK_SIZE))

    def wall_grid(self):
        """坦克格 -> 墙体类型；砖块被打掉后按墙体数量自动重建。

        碰撞检测用的是半格，但 AI 是按车身走位的，所以这里把半格合并成
        坦克格：只要这个坦克格里还剩任意一块墙，就当成实心对待。
        宁可保守一点，也别让 AI 规划出一条走不过去的路。
        """
        if self._cell_walls is None or self._wall_count != len(self.walls):
            cells = {}
            for wall in self.walls:
                start_col = int((wall.rect.x - self.offset_x) // TILE_SIZE)
                start_row = int((wall.rect.y - self.offset_y) // TILE_SIZE)
                span_cols = max(1, wall.rect.width // TILE_SIZE)
                span_rows = max(1, wall.rect.height // TILE_SIZE)
                for dc in range(span_cols):
                    for dr in range(span_rows):
                        block = ((start_col + dc) // 2, (start_row + dr) // 2)
                        if wall.wall_type in (STEEL, BASE):
                            cells[block] = wall.wall_type      # 硬墙覆盖砖墙
                        elif cells.get(block) not in (STEEL, BASE):
                            cells[block] = BRICK
            self._cell_walls = cells
            self._wall_count = len(self.walls)
        return self._cell_walls

    def find_path(self, start, goal):
        """带代价的 Dijkstra，返回从 start 的下一格到 goal 的路径。

        空地代价 1，**砖墙代价 AI_BRICK_COST**（要停下来打几炮才通），
        钢墙和老鹰不可通行。这样 AI 自己会算：“绕远路”和“打穿一堵墙”哪个更快。
        """
        if goal == start:
            return []
        grid = self.wall_grid()
        occupied = {self.cell_of(enemy.rect) for enemy in self.enemies} - {goal}
        frontier = [(0, 0, start)]
        came_from = {start: None}
        best = {start: 0}
        while frontier:
            cost, _, cell = heapq.heappop(frontier)
            if cell == goal:
                path = []
                while cell != start:
                    path.append(cell)
                    cell = came_from[cell]
                path.reverse()
                return path
            if cost > best.get(cell, 1 << 30):
                continue
            for dcol, drow in ((0, -1), (0, 1), (-1, 0), (1, 0)):
                nxt = (cell[0] + dcol, cell[1] + drow)
                if not (0 <= nxt[0] < MAP_COLS // 2 and 0 <= nxt[1] < MAP_ROWS // 2):
                    continue
                if nxt in occupied:
                    continue
                kind = grid.get(nxt)
                if kind in (STEEL, BASE):
                    continue
                new_cost = cost + (AI_BRICK_COST if kind == BRICK else 1)
                if new_cost >= best.get(nxt, 1 << 30):
                    continue
                best[nxt] = new_cost
                came_from[nxt] = cell
                heapq.heappush(frontier, (new_cost, len(came_from), nxt))
        return None

    def ai_shot_line_clear(self, enemy):
        """与敌人同排/同列、且中间没有任何墙时返回 True。

        砖墙也算阻挡：这个游戏里子弹碰到砖就没了，一炮只打掉半格砖。
        以前把砖当“能打穿、不算阻挡”，结果 AI 站在墙后面自认为在点射，
        其实每一炮都喂给了墙 —— 打墙要由寻路阶段显式决定，不能混在开火判断里。
        """
        player = self.player.rect
        player_x, player_y = player.center
        enemy_x, enemy_y = enemy.rect.center
        cell = self.cell_of(player)
        goal = self.cell_of(enemy.rect)
        grid = self.wall_grid()

        if abs(player_y - enemy_y) <= AI_SHOOT_TOLERANCE and abs(player_x - enemy_x) > BLOCK_SIZE // 2:
            step = 1 if enemy_x > player_x else -1
            for col in range(cell[0] + step, goal[0] + step, step):
                if (col, cell[1]) in grid:
                    return False
            return True

        if abs(player_x - enemy_x) <= AI_SHOOT_TOLERANCE and abs(player_y - enemy_y) > BLOCK_SIZE // 2:
            step = 1 if enemy_y > player_y else -1
            for row in range(cell[1] + step, goal[1] + step, step):
                if (cell[0], row) in grid:
                    return False
            return True
        return False

    def ai_shoot(self):
        if self.player.cooldown > 0:
            return False
        for enemy in self.enemies:
            if not self.ai_shot_line_clear(enemy):
                continue
            # 先转炮口再开火：子弹是朝车身朝向飞出去的。
            # 不转身的话，原地“点射”其实是在对着旁边的墙打，永远打不中。
            dx = enemy.rect.centerx - self.player.rect.centerx
            dy = enemy.rect.centery - self.player.rect.centery
            if abs(dx) >= abs(dy):
                self.player.set_direction(RIGHT if dx > 0 else LEFT)
            else:
                self.player.set_direction(DOWN if dy > 0 else UP)
            if self.player.can_shoot():
                self.shoot(self.player)
                return True
        return False

    def ai_danger(self):
        """返回最紧迫的一发“正对着我们”的敌方子弹：(剩余距离, dx, dy, x, y)。"""
        player = self.player.rect
        worst = None
        for bullet in self.bullets:
            if bullet.owner != ENEMY:
                continue
            dx, dy = bullet.direction
            bullet_x, bullet_y = bullet.rect.center
            if dy == 0:
                if abs(bullet_y - player.centery) > AI_DODGE_CLEARANCE:
                    continue
                ahead = (player.centerx - bullet_x) * dx
            else:
                if abs(bullet_x - player.centerx) > AI_DODGE_CLEARANCE:
                    continue
                ahead = (player.centery - bullet_y) * dy
            if 0 < ahead <= AI_THREAT_RANGE and (worst is None or ahead < worst[0]):
                worst = (ahead, dx, dy, bullet_x, bullet_y)
        return worst

    def ai_can_step(self, direction):
        dx, dy = direction
        probe = self.player.rect.move(dx * self.player.speed, dy * self.player.speed)
        # 光看碰撞不够：贴着场地边缘时这一步会被 clamp 吃掉，
        # 但 check_collision 说“没事”，AI 就会一直顶着边界推。
        if not PLAY_RECT.contains(probe):
            return False
        return not self.player.check_collision(self.walls, self.all_sprites, probe)

    def ai_clearance(self, direction, steps=4):
        """沿某个方向还能走几步（一步=2 逻辑像素），用来判断往哪边躲空间更大。"""
        dx, dy = direction
        rect = self.player.rect
        for step in range(1, steps + 1):
            probe = rect.move(dx * self.player.speed * step, dy * self.player.speed * step)
            if self.player.check_collision(self.walls, self.all_sprites, probe):
                return step - 1
        return steps

    def ai_lateral_room(self, direction, limit=px(40)):
        """朝这个方向还能挪多少像素，用来判断“够不够让开一条弹道”。"""
        dx, dy = direction
        rect = self.player.rect
        room = 0
        steps = max(1, limit // max(1, self.player.speed))
        for step in range(1, steps + 1):
            probe = rect.move(dx * self.player.speed * step, dy * self.player.speed * step)
            if not PLAY_RECT.contains(probe):
                break
            if self.player.check_collision(self.walls, self.all_sprites, probe):
                break
            room = step * self.player.speed
        return room

    def ai_brick_ahead(self, direction):
        """朝这个方向的相邻格是不是砖墙（只有砖能被打穿）。"""
        cell = self.cell_of(self.player.rect)
        grid = self.wall_grid()
        return grid.get((cell[0] + direction[0], cell[1] + direction[1])) == BRICK

    def ai_shoot_block(self, direction):
        """转身朝这个方向开一炮，用来打穿砖墙。冷却没好也先转过去。"""
        self.player.set_direction(direction)
        if self.player.cooldown > 0 or not self.player.can_shoot():
            return False
        self.shoot(self.player)
        return True

    def ai_evade(self):
        """有子弹朝我们飞来时，垂直于弹道让开；侧面让不开就把砖打穿。

        方向一旦选定就锁住若干帧：否则刚让开一点就回头赶路，
        正好又撞回同一发子弹的弹道。
        """
        # 已经决定砸墙了就先把这个洞打通，别中途改主意（改一次洞就白打了）
        if self.ai_break_hold > 0:
            if self.ai_brick_ahead(self.ai_break_dir):
                self.ai_break_hold -= 1
                self.ai_shoot_block(self.ai_break_dir)
                return STAY
            self.ai_break_hold = 0

        danger = self.ai_danger()
        if danger is not None:
            _, dx, dy, bullet_x, bullet_y = danger
            player = self.player.rect
            if dy:
                offset = player.centerx - bullet_x
                options = (RIGHT, LEFT) if offset > 0 else (LEFT, RIGHT)
            else:
                offset = player.centery - bullet_y
                options = (DOWN, UP) if offset > 0 else (UP, DOWN)

            # 两侧各能挪多远；够半个车身才算真的让得开
            best_direction, best_room = None, 0
            for rank, direction in enumerate(options):
                room = self.ai_lateral_room(direction) - rank * px(2)
                if room > best_room:
                    best_room, best_direction = room, direction

            # 侧面根本让不开（走廊、夹缝）：顺着通道跑是跑不过子弹的。
            # 旁边的砖可以直接打穿，那就打穿它钻出去，而不是回头找现成的路口。
            if best_room < AI_DODGE_CLEARANCE and danger[0] < AI_BREAK_RANGE:
                for direction in options:
                    if self.ai_brick_ahead(direction):
                        self.ai_break_dir = direction
                        self.ai_break_hold = AI_BREAK_HOLD
                        self.ai_shoot_block(direction)
                        return STAY

            if best_direction is not None:
                self.ai_dodge = AI_DODGE_FRAMES
                self.ai_dodge_dir = best_direction
                return best_direction

        if self.ai_dodge > 0:
            self.ai_dodge -= 1
            if self.ai_dodge_dir and self.ai_can_step(self.ai_dodge_dir):
                return self.ai_dodge_dir
            self.ai_dodge = 0
        return None

    def ai_plan(self, cell):
        """选目标敌人并返回路径。

        上一次的目标只要没被明显甩开就继续用 —— 两台敌人距离接近时，
        每帧重算会来回换目标，路也跟着来回改，车身就开始抽。
        """
        paths = []
        for enemy in self.enemies:
            path = self.find_path(cell, self.cell_of(enemy.rect))
            if path is not None:
                paths.append((len(path), enemy, path))
        if not paths:
            self.ai_target = None
            return None
        paths.sort(key=lambda item: item[0])

        if self.ai_target is not None and self.ai_target in self.enemies:
            for length, enemy, path in paths:
                if enemy is self.ai_target and length <= paths[0][0] + AI_TARGET_STICKY:
                    return path
        self.ai_target = paths[0][1]
        return paths[0][2]

    def ai_direct_direction(self, cell):
        """没有通路时的兜底：朝敌人方向硬推，优先走能走的那一轴。"""
        if not self.enemies:
            return None
        player = self.player.rect
        enemy = min(self.enemies,
                    key=lambda item: abs(item.rect.centerx - player.centerx)
                    + abs(item.rect.centery - player.centery))
        goal = self.cell_of(enemy.rect)
        dcol = (goal[0] > cell[0]) - (goal[0] < cell[0])
        drow = (goal[1] > cell[1]) - (goal[1] < cell[1])
        for direction in ((dcol, 0), (0, drow)):
            if direction != (0, 0) and self.ai_can_step(direction):
                return direction
        return None

    def ai_hold_distance(self):
        """已经能从射程外打到敌人时就稳在原地点射，贴脸打反应不过来。

        判定必须和 ai_shoot 用的是同一把尺子（ai_shot_line_clear）：
        否则会出现“认为自己在瞄准所以站住，但实际开不出火”的干站。
        """
        player = self.player.rect
        for enemy in self.enemies:
            if not self.ai_shot_line_clear(enemy):
                continue
            gap = (abs(enemy.rect.centerx - player.centerx)
                   + abs(enemy.rect.centery - player.centery))
            if gap >= AI_HOLD_DISTANCE:
                return True
        return False

    def ai_peek(self):
        """炮弹还没冷却好时，先侧移离开近处敌人的射线，别站在枪口上等装填。

        方向要挑“空间更大的那一侧”并锁几帧。以前每帧取第一个能走的方向，
        挪一步撞墙就换另一边，于是原地左右抖 —— 挪了等于没挪。
        """
        if self.ai_peek_hold > 0:
            self.ai_peek_hold -= 1
            if self.ai_peek_dir and self.ai_can_step(self.ai_peek_dir):
                return self.ai_peek_dir
            self.ai_peek_hold = 0
        if self.player.cooldown <= AI_PEEK_COOLDOWN:
            return None
        player = self.player.rect
        for enemy in self.enemies:
            gap = (abs(enemy.rect.centerx - player.centerx)
                   + abs(enemy.rect.centery - player.centery))
            if gap > AI_PEEK_RANGE or not self.ai_shot_line_clear(enemy):
                continue
            if abs(player.centery - enemy.rect.centery) <= AI_SHOOT_TOLERANCE:
                options = ((0, 1), (0, -1))
            else:
                options = ((1, 0), (-1, 0))
            best, best_room = None, 0
            for direction in options:
                room = self.ai_lateral_room(direction, px(24))
                if room > best_room:
                    best_room, best = room, direction
            # 挤在一条刚够车身的缝里就别侧移了，挪 4 像素躲不开，只会抖
            if best is not None and best_room >= px(10) and self.ai_can_step(best):
                self.ai_peek_dir = best
                self.ai_peek_hold = AI_PEEK_HOLD
                return best
        return None

    def ai_advance(self):
        """沿路径逼近敌人。

        不在格心附近时优先沿当前朝向继续走；但朝向走不通就必须先回格心对位。
        否则坦克被拐角辅助推开半个格子后，会一直顶着那堵墙推到天荒地老。
        """
        rect = self.player.rect
        cell = self.cell_of(rect)
        anchor_x = self.offset_x + cell[0] * BLOCK_SIZE
        anchor_y = self.offset_y + cell[1] * BLOCK_SIZE

        if (abs(rect.x - anchor_x) > AI_TURN_TOLERANCE
                or abs(rect.y - anchor_y) > AI_TURN_TOLERANCE):
            if self.ai_heading and self.ai_can_step(self.ai_heading):
                return self.ai_heading
            if abs(rect.x - anchor_x) >= abs(rect.y - anchor_y):
                return (1 if anchor_x > rect.x else -1, 0)
            return (0, 1 if anchor_y > rect.y else -1)

        if self.ai_hold_distance():
            self.ai_heading = None
            return STAY

        self.ai_replan -= 1
        if self.ai_path is None or self.ai_replan <= 0:
            self.ai_path = self.ai_plan(cell)
            self.ai_replan = AI_REPLAN_INTERVAL
        while self.ai_path and self.ai_path[0] == cell:
            self.ai_path.pop(0)
        # 坦克被躲弹/拐角辅助推到了别的格子，缓存路径的下一格可能变成斜对角。
        # 斜着走会同时改两轴、还会把炮口摆成斜的，必须立刻重算。
        if self.ai_path and (abs(self.ai_path[0][0] - cell[0])
                             + abs(self.ai_path[0][1] - cell[1]) != 1):
            self.ai_path = self.ai_plan(cell)
            self.ai_replan = AI_REPLAN_INTERVAL
            while self.ai_path and self.ai_path[0] == cell:
                self.ai_path.pop(0)

        direction = None
        if self.ai_path:
            nxt = self.ai_path[0]
            if not any(self.cell_of(enemy.rect) == nxt for enemy in self.enemies):
                if self.wall_grid().get(nxt) == BRICK:
                    # 路径穿过砖墙：先精确对到格心再开炮。
                    # 差半个车身的话炮口会偏到砖块一侧，永远只打掉同一排砖，
                    # 洞口永远只有半格高，车过不去 —— 这也是之前“打半天不穿”的原因。
                    if abs(rect.x - anchor_x) > px(2) or abs(rect.y - anchor_y) > px(2):
                        if abs(rect.x - anchor_x) >= abs(rect.y - anchor_y):
                            return (1 if anchor_x > rect.x else -1, 0)
                        return (0, 1 if anchor_y > rect.y else -1)
                    self.ai_shoot_block((nxt[0] - cell[0], nxt[1] - cell[1]))
                    self.ai_heading = None
                    return STAY
                direction = (nxt[0] - cell[0], nxt[1] - cell[1])
        if direction is None:
            direction = self.ai_direct_direction(cell)
        self.ai_heading = direction
        return direction

    def ai_drive(self, direction):
        dx, dy = direction
        if abs(dx) + abs(dy) != 1:       # 只走四方向，别把车开成斜的
            return False
        self.player.set_direction(direction)
        return self.player.move(dx * self.player.speed, dy * self.player.speed,
                                self.walls, self.all_sprites)

    def auto_play(self):
        """托管模式下代替玩家操作。

        注意 STAY 和 None 是两回事：STAY 是“主动站住”（定点开火/砸墙），
        None 才是“没主意”。以前把两者混在一起，站定点射时会被当成没主意
        随手挪一步，于是每帧在两点之间来回抽。
        """
        direction = self.ai_evade()
        if direction is STAY:
            return
        self.ai_shoot()
        if direction is None:
            direction = self.ai_peek()
        if direction is None:
            direction = self.ai_advance()
        if direction is STAY:
            return
        if direction is None:
            direction = next((d for d in (UP, DOWN, LEFT, RIGHT)
                              if self.ai_can_step(d)), None)
        if direction is None:
            return
        if self.ai_drive(direction):
            return
        # 想走的方向被挡住就换一条，别在原地耗着
        self.ai_path = None
        for alternative in (UP, DOWN, LEFT, RIGHT):
            if alternative != direction and self.ai_can_step(alternative):
                if self.ai_drive(alternative):
                    self.ai_heading = alternative
                return

    # ---------- 绘制 ----------

    def draw(self):
        self.canvas.fill(DARK)
        self.draw_battlefield()

        self.walls.draw(self.canvas)
        self.all_sprites.draw(self.canvas)

        self.draw_hud()
        self.draw_footer()

        if self.game_over:
            self.draw_game_over()
        elif self.paused:
            self.draw_pause()

        self.present()

    def draw_battlefield(self):
        """平铺底色 + 极淡的网格，让战场有尺度感但不抢眼。"""
        pygame.draw.rect(self.canvas, BLACK, PLAY_RECT)
        # 网格按“坦克格”画（2×2 半格）：半格只有 20px，逐格画会糊成一片
        for col in range(1, MAP_COLS // 2):
            line_x = PLAY_RECT.left + col * BLOCK_SIZE
            pygame.draw.line(self.canvas, GRID_LINE,
                             (line_x, PLAY_RECT.top), (line_x, PLAY_RECT.bottom - 1), px(1))
        for row in range(1, MAP_ROWS // 2):
            line_y = PLAY_RECT.top + row * BLOCK_SIZE
            pygame.draw.line(self.canvas, GRID_LINE,
                             (PLAY_RECT.left, line_y), (PLAY_RECT.right - 1, line_y), px(1))
        # 场地边框：原版那一圈钢边。画在场地最外沿，正好压在外圈那两格空地上
        pygame.draw.rect(self.canvas, shade(PANEL, 1.55), PLAY_RECT, px(7))
        pygame.draw.rect(self.canvas, PANEL_LIGHT, PLAY_RECT, px(1))

    def draw_hud(self):
        hud = pygame.Rect(0, 0, SCREEN_WIDTH, PLAY_RECT.top)
        pygame.draw.rect(self.canvas, PANEL, hud)
        pygame.draw.line(self.canvas, PANEL_LIGHT,
                         (0, hud.bottom - px(1)), (SCREEN_WIDTH, hud.bottom - px(1)), px(2))

        mid_y = hud.centery
        cursor = PLAY_RECT.left

        # 左边：生命值方块
        label = self.font_small.render("HP", True, TEXT_MUTED)
        self.canvas.blit(label, (cursor, mid_y - label.get_height() // 2))
        cursor += label.get_width() + px(12)
        for index in range(self.max_hp):
            block = pygame.Rect(cursor, mid_y - px(9), px(16), px(18))
            color = GREEN if index < self.player.hp else PANEL_LIGHT
            pygame.draw.rect(self.canvas, color, block, border_radius=px(3))
            cursor += px(20)

        # 中间：剩余敌人方块
        label = self.font_small.render("ENEMY", True, TEXT_MUTED)
        icon, gap = px(12), px(8)
        group_w = label.get_width() + px(12) + self.total_enemies * (icon + gap) - gap
        cursor = SCREEN_WIDTH // 2 - group_w // 2
        self.canvas.blit(label, (cursor, mid_y - label.get_height() // 2))
        cursor += label.get_width() + px(12)
        for index in range(self.total_enemies):
            block = pygame.Rect(cursor + index * (icon + gap), mid_y - icon // 2, icon, icon)
            color = RED if index < len(self.enemies) else PANEL_LIGHT
            pygame.draw.rect(self.canvas, color, block, border_radius=px(2))

        # 右边：退出提示
        hint = self.font_small.render("ESC  QUIT", True, TEXT_MUTED)
        self.canvas.blit(hint, (PLAY_RECT.right - hint.get_width(),
                                mid_y - hint.get_height() // 2))

    def draw_footer(self):
        footer = pygame.Rect(0, PLAY_RECT.bottom, SCREEN_WIDTH, SCREEN_HEIGHT - PLAY_RECT.bottom)
        pygame.draw.rect(self.canvas, PANEL, footer)
        pygame.draw.line(self.canvas, PANEL_LIGHT,
                         (0, footer.top), (SCREEN_WIDTH, footer.top), px(1))

        if self.paused:
            text = self.font_small.render("ENTER  RESUME", True, YELLOW)
        elif self.auto_pilot:
            text = self.font_small.render("AI AUTO-PILOT        T  BACK TO MANUAL", True, YELLOW)
        else:
            text = self.font_small.render(
                "MOVE  W A S D / ARROW KEYS        FIRE  SPACE / LEFT CLICK        "
                "T  AUTO        ENTER  PAUSE",
                True, TEXT_MUTED)
        self.canvas.blit(text, text.get_rect(center=footer.center))

    def draw_pause(self):
        veil = pygame.Surface(PLAY_RECT.size, pygame.SRCALPHA)
        veil.fill((0, 0, 0, 150))
        self.canvas.blit(veil, PLAY_RECT.topleft)

        panel = pygame.Rect(0, 0, px(240), px(110))
        panel.center = PLAY_RECT.center
        pygame.draw.rect(self.canvas, PANEL, panel, border_radius=px(10))
        pygame.draw.rect(self.canvas, PANEL_LIGHT, panel, px(2), border_radius=px(10))

        title = self.font_title.render("PAUSED", True, YELLOW)
        self.canvas.blit(title, title.get_rect(center=panel.center))

    def draw_game_over(self):
        veil = pygame.Surface(PLAY_RECT.size, pygame.SRCALPHA)
        veil.fill((0, 0, 0, 170))
        self.canvas.blit(veil, PLAY_RECT.topleft)

        panel = pygame.Rect(0, 0, px(340), px(180))
        panel.center = PLAY_RECT.center
        pygame.draw.rect(self.canvas, PANEL, panel, border_radius=px(10))
        pygame.draw.rect(self.canvas, PANEL_LIGHT, panel, px(2), border_radius=px(10))

        title_text = "YOU WIN" if self.win else "GAME OVER"
        title_color = GREEN if self.win else RED
        title = self.font_title.render(title_text, True, title_color)
        self.canvas.blit(title, title.get_rect(center=(panel.centerx, panel.top + px(64))))

        tip = self.font.render("Press R to restart", True, TEXT_MUTED)
        self.canvas.blit(tip, tip.get_rect(center=(panel.centerx, panel.bottom - px(54))))

    def present(self):
        """把超采样画布一次性平滑缩放到窗口，这一步决定最终清晰度。"""
        window_size = self.screen.get_size()
        if window_size == (SCREEN_WIDTH, SCREEN_HEIGHT):
            self.screen.blit(self.canvas, (0, 0))
        else:
            pygame.transform.smoothscale(self.canvas, window_size, self.screen)
        pygame.display.flip()

    def run(self):
        while self.running:
            self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
        pygame.quit()
        sys.exit()


if __name__ == "__main__":
    game = TankGame()
    game.run()
