import pygame
import random
import collections

import pickle
import os
from typing import List, Dict, Optional, Deque, Any, Tuple, cast

# --- MASTER SCALING FACTOR ---
SCALE = 1

# --- CONFIGURATION ---
SCREEN_WIDTH = int(1200 * SCALE)
SCREEN_HEIGHT = int(1000 * SCALE)
FPS = 60

HIGHWAY_Y = int(220 * SCALE)
LANE_SPACING = int(360 * SCALE) # Distance between Lane 0 and Lane 1
HIST_Y = int(SCREEN_HEIGHT - (185 * SCALE))
HIST_HEIGHT = int(160 * SCALE)

DOT_RADIUS = int(5 * SCALE) 
BLADE_W = int(6 * SCALE)
BLADE_H = int(20 * SCALE)
RACK_PAD = int(40 * SCALE)

BG_COLOR = (20, 20, 30)
BLACK = (0, 0, 0)
ROAD_COLOR = (50, 50, 60)
DOT_GRAY = (150, 150, 150)
RED = (231, 76, 60)       
GREEN = (46, 204, 113)    
BLUE = (52, 152, 219)     
ORANGE = (243, 156, 18)   
PURPLE = (155, 89, 182)   
CYAN = (22, 160, 133)     
YELLOW = (241, 196, 15)
TEXT_WHITE = (220, 220, 220)
HIGHLIGHT = (241, 196, 15) # The "Active" Color
MUTED = (100, 100, 100)
DIM_GRAY = (105, 105, 105)
DARK_SERVER = (40, 40, 50)
PEN_COLOR = (60, 60, 70)
WHITE = (255, 255, 255)
PANEL_BG = (30, 30, 40, 220) 
CYAN_BUST = (0, 255, 255) # Bright Cyan for Busters

KEYS_PER_STAGE = 7
DEFAULT_RPS = 1
DEFAULT_WORKERS = 8     
MAX_WORKERS = 24        
ROW_CAPACITY = 8        # Narrower rack (saves horizontal space)
ROW_V_SPACING = 45      # Vertical spacing between rows (allows room for overhead reqs)
DEFAULT_WORK_TIME = 1.0         
MIN_LATENCY = 3.5  
INITIAL_MAX_LATENCY = 6.0
MAX_STAGES = 5
MAX_LANES = 2 

class Request:
    def __init__(self, creation_time, lane_idx=0, is_ghost=False, ghost_key=None, ghost_stage=None, is_tracer=False, is_bust=False, key_limit=KEYS_PER_STAGE):
        self.creation_time = creation_time
        self.lane_idx = lane_idx # 0 or 1
        self.is_ghost = is_ghost
        self.is_leader = False
        self.is_tracer = is_tracer
        self.is_bust = is_bust # Surgical Cache Bust
        self.stage_idx_ref: Optional['Stage'] = None # Forward reference to Stage class 
        
        if is_ghost:
            self.state = 'diverting' 
            self.x = ghost_stage.x_trigger
            self.y = HIGHWAY_Y + (int(15 * SCALE) * ghost_stage.y_offset_dir)
            self.keys = [ghost_key] * MAX_STAGES 
            self.stage_idx = ghost_stage.index
            self.base_speed = 7.5 * SCALE 
            self.stage_idx_ref = ghost_stage
        else:
            # Determine Y based on Lane
            base_y = HIGHWAY_Y if lane_idx == 0 else HIGHWAY_Y + LANE_SPACING
            
            self.state = 'highway'
            self.x = 0
            self.y = base_y + random.randint(int(-8 * SCALE), int(8 * SCALE))
            self.base_speed = 7.5 * SCALE 
            self.stage_idx = 0
            # Respect the active limit for new requests
            self.keys = [random.randint(0, key_limit-1) for _ in range(MAX_STAGES)]

    def get_current_key(self, max_stages):
        idx = min(self.stage_idx, max_stages - 1)
        return self.keys[idx]

class BackendWorker:
    def __init__(self):
        self.busy_until = 0
        self.current_reqs = [] 

class Stage:
    def __init__(self, index, x_trigger, y_offset_dir):
        self.index = index
        self.x_trigger = int(x_trigger) 
        self.y_offset_dir = y_offset_dir 
        self.worker_y_base = HIGHWAY_Y + (int(120 * SCALE) * y_offset_dir)
        
        # SHARED RESOURCES
        self.workers: List[BackendWorker] = [BackendWorker() for _ in range(DEFAULT_WORKERS)]
        self.worker_queue: Deque[List[Request]] = collections.deque() # Unified Queue (Always contains Lists of Reqs)
        self.l2_cache: Dict[int, float] = {} # Key -> Expiry (SHARED)
        
        # CONFIG
        self.work_time = DEFAULT_WORK_TIME 
        self.ttl = 4.0
        self.refresh_time = 2.0
        self.cache_enabled = True
        self.refresh_enabled = False
        self.coalesce_enabled = False
        self.batch_enabled = False 
        self.l2_enabled = False
        self.batch_window = 0.2
        self.batch_max_size = 4
        self.jitter = 0.0 # Random variation in TTLs (Seconds)

        # PER-LANE RESOURCES (Split State)
        self.l1_caches: Dict[int, Dict[int, float]] = {i: {} for i in range(MAX_LANES)}
        self.refresh_active: Dict[int, Dict[int, bool]] = {i: {} for i in range(MAX_LANES)}
        self.leaders_inflight: Dict[int, Dict[int, bool]] = {i: {} for i in range(MAX_LANES)}
        self.batch_buffers: Dict[int, List[Request]] = {i: [] for i in range(MAX_LANES)}
        self.batch_timers: Dict[int, float] = {i: 0 for i in range(MAX_LANES)}
        self.coalesce_counts: Dict[int, int] = {i: 0 for i in range(MAX_LANES)}
        
        # Waiting queues are separate per lane to wake up correct ghosts
        self.waiting_for_refresh: Dict[int, Dict[int, List[Request]]] = {i: cast(Dict[int, List[Request]], collections.defaultdict(list)) for i in range(MAX_LANES)}

        # Load History
        self.load_history = collections.deque(maxlen=100)
        self.stats_timer = 0


    def adjust_capacity(self, delta):
        new_len = len(self.workers) + delta
        if 1 <= new_len <= MAX_WORKERS:
            if delta > 0: self.workers.append(BackendWorker())
            else:
                idle = [w for w in self.workers if w.busy_until == 0]
                if idle: self.workers.remove(idle[0])
                else: 
                    # If we must remove a busy worker, save its request!
                    popped_worker = self.workers.pop()
                    if popped_worker.current_reqs:
                        for req in popped_worker.current_reqs:
                            req.state = 'queued_for_worker'
                            # Add to FRONT of queue (priority)
                            # worker_queue is a deque of LISTS (batches)
                            self.worker_queue.appendleft([req])

    def get_jittered_ttl(self):
        if self.jitter <= 0: return self.ttl
        return max(0.1, self.ttl + random.uniform(-self.jitter, self.jitter))

    def reset(self):
        # Reset Per-Lane State
        for i in range(MAX_LANES):
            self.l1_caches[i] = {}
            self.refresh_active[i] = {}
            self.leaders_inflight[i] = {}
            self.batch_buffers[i] = []
            self.batch_timers[i] = 0
            self.coalesce_counts[i] = 0

            self.waiting_for_refresh[i] = cast(Dict[int, List[Request]], collections.defaultdict(list))
            
        self.load_history.clear()
        self.stats_timer = 0

            
        # Reset Shared State
        self.worker_queue.clear()
        self.l2_cache = {}
        for w in self.workers:
            w.busy_until = 0
            w.current_reqs = []

    def is_cached(self, lane_idx, key, time):
        cache = self.l1_caches[lane_idx]
        return key in cache and time < cache[key]

    def is_l2_cached(self, key, time):
        return key in self.l2_cache and time < self.l2_cache[key]

    def update(self, dt, sim_time):
        # Iterate over all lanes to handle local logic
        for lane in range(MAX_LANES):
            # 1. Cache Expiry Maintenance
            for key in list(self.refresh_active[lane].keys()):
                 cache = self.l1_caches[lane]
                 if sim_time > cache.get(key, 0) + (self.ttl * 2):
                    self.refresh_active[lane].pop(key, None)

            # 2. Batch Lifecycle
            if self.batch_enabled:
                buffer = self.batch_buffers[lane]
                if len(buffer) > 0:
                    time_trigger = (sim_time - self.batch_timers[lane]) >= self.batch_window
                    size_trigger = len(buffer) >= self.batch_max_size
                    
                    if time_trigger or size_trigger:
                        # Seal the batch
                        batch_size = min(len(buffer), self.batch_max_size)
                        sealed_batch = list(buffer)[:batch_size] # type: ignore
                        self.batch_buffers[lane] = list(buffer)[batch_size:] # type: ignore
                        
                        # Add to SHARED Worker Queue
                        self.worker_queue.append(sealed_batch)
                        for r in sealed_batch: r.state = 'queued_for_worker'
                        
                        # Reset Timer
                        self.batch_timers[lane] = sim_time if len(self.batch_buffers[lane]) > 0 else 0

        # 2a. Load Statistics
        self.stats_timer += dt
        if self.stats_timer >= 0.1:
             self.stats_timer = 0
             busy = sum(1 for w in self.workers if w.busy_until > sim_time)
             total = len(self.workers)
             pct = busy / total if total > 0 else 0
             self.load_history.append(pct)

        # 3. WORKER ASSIGNMENT (Unified & Shared)
        if self.worker_queue:
            # WORKER SERVE: Simple FIFO. L2 filtering happened upstream.
            free_workers = [w for w in self.workers if w.busy_until < sim_time and len(w.current_reqs) == 0]
            if free_workers:
                worker = free_workers[0]
                unit_of_work = self.worker_queue.popleft()
                
                latency = self.work_time + (0.05 * len(unit_of_work))
                worker.busy_until = sim_time + latency
                worker.current_reqs = unit_of_work
                for r in unit_of_work: r.state = 'processing'


def get_fonts() -> Dict[str, Any]:
    return {
        'title': pygame.font.SysFont("Arial", int(24*SCALE), bold=True),
        'std': pygame.font.SysFont("Consolas", int(14*SCALE), bold=False),
        'key': pygame.font.SysFont("Arial", int(12*SCALE), bold=True),
        'small': pygame.font.SysFont("Consolas", int(10*SCALE), bold=False),
        'tiny': pygame.font.SysFont("Arial", int(10*SCALE), bold=True),
        'mono': pygame.font.SysFont("Consolas", int(12*SCALE), bold=True), 
        'blob': pygame.font.SysFont("Arial", int(20*SCALE), bold=True),
        'big': pygame.font.SysFont("Arial", int(24*SCALE), bold=True),
    }

def draw_text(surface: Any, font: Any, text: str, color: Tuple[int, int, int], pos: Tuple[int, int], align: str = 'topleft') -> None:
    # Helper to handle text rendering and blitting with alignment
    surf = font.render(text, True, color)
    rect = surf.get_rect()
    setattr(rect, align, pos)
    surface.blit(surf, rect)

def draw_shape(surface, color, x, y, shape_id, radius, width=0):
    shape_id = shape_id % 7 # Safety wrap
    if shape_id == 0: pygame.draw.circle(surface, color, (x, y), radius, width)
    elif shape_id == 1: pygame.draw.rect(surface, color, (x - radius, y - radius, radius*2, radius*2), width)
    elif shape_id == 2: pygame.draw.polygon(surface, color, [(x, y - radius), (x - radius, y + radius), (x + radius, y + radius)], width)
    elif shape_id == 3: pygame.draw.polygon(surface, color, [(x, y - radius), (x + radius, y), (x, y + radius), (x - radius, y)], width)
    elif shape_id == 4: 
        pts = [(x, y-radius), (x-radius, y-radius//3), (x-radius//2, y+radius), (x+radius//2, y+radius), (x+radius, y-radius//3)]
        pygame.draw.polygon(surface, color, pts, width)
    elif shape_id == 5: 
        pts = [(x-radius, y), (x-radius//2, y-radius), (x+radius//2, y-radius), (x+radius, y), (x+radius//2, y+radius), (x-radius//2, y+radius)]
        pygame.draw.polygon(surface, color, pts, width)
    elif shape_id == 6:
        pygame.draw.polygon(surface, color, [(x-radius, y-radius), (x+radius, y-radius), (x, y+radius)], width)

def draw_key_glyph(surface, fonts, text, x, y, width=int(30*SCALE)):
    height = int(30*SCALE)
    pygame.draw.rect(surface, (60, 60, 70), (x, y, width, height), border_radius=5)
    pygame.draw.rect(surface, (100, 100, 110), (x, y, width, height), 2, border_radius=5)
    txt_surf = fonts['key'].render(text, True, WHITE)
    surface.blit(txt_surf, (x + (width - txt_surf.get_width()) // 2, y + (height - txt_surf.get_height()) // 2))
    return x + width + int(10*SCALE)

def get_state_color(req):
    if req.is_ghost: return WHITE 
    if req.state == 'highway': return DOT_GRAY 
    if req.state == 'waiting_coalesce': return PURPLE
    if req.state == 'diverting': return ORANGE
    if req.state == 'in_pen': return BLUE
    if req.state == 'queued_for_worker': return BLUE
    if req.state == 'l2_serving': return CYAN
    if req.state == 'processing': return RED
    if req.state == 'returning': return GREEN
    if req.state == 'waiting_refresh': return YELLOW
    if req.state == 'done': return GREEN
    return DOT_GRAY

def draw_histogram(screen, latencies, fonts):
    pygame.draw.rect(screen, (30, 30, 40), (int(50*SCALE), HIST_Y, SCREEN_WIDTH - int(100*SCALE), HIST_HEIGHT))
    pygame.draw.line(screen, (100, 100, 100), (int(50*SCALE), HIST_Y + HIST_HEIGHT), (SCREEN_WIDTH - int(50*SCALE), HIST_Y + HIST_HEIGHT), 2)
    if not latencies: return

    data_list = list(latencies)
    data_list.sort()
    count = len(data_list)
    min_lat = data_list[0]
    avg_lat = sum(data_list) / count
    p50 = data_list[int(count * 0.5)]
    p95 = data_list[int(count * 0.95)]
    max_observed = data_list[-1]
    
    current_graph_max = max(INITIAL_MAX_LATENCY, float(max_observed) * 1.1)

    stats_text = f"N:{count} | Min:{min_lat:.2f}s | Avg:{avg_lat:.2f}s | P50:{p50:.2f}s | P95:{p95:.2f}s | Max:{max_observed:.2f}s"
    draw_text(screen, fonts['std'], stats_text, TEXT_WHITE, (int(60*SCALE), HIST_Y + int(10*SCALE)))
    
    # Draw Graph
    draw_text(screen, fonts['std'], f"{MIN_LATENCY}s", MUTED, (int(50*SCALE), HIST_Y + HIST_HEIGHT + 5))
    draw_text(screen, fonts['std'], f"{current_graph_max:.1f}s", MUTED, (SCREEN_WIDTH - int(120*SCALE), HIST_Y + HIST_HEIGHT + 5))

    bar_width = max(2, int(6 * SCALE))
    graph_width = SCREEN_WIDTH - int(120*SCALE)
    num_bars = int(graph_width / bar_width)
    bins = [0] * num_bars
    latency_range = current_graph_max - MIN_LATENCY
    
    for lat in latencies:
        if lat < MIN_LATENCY: normalized = 0
        elif lat >= current_graph_max: normalized = 0.99
        else: normalized = (lat - MIN_LATENCY) / latency_range
        idx = int(normalized * num_bars)
        if idx >= num_bars: idx = num_bars - 1
        bins[idx] += 1
        
    max_bin_height = max(bins) if bins else 1
    for i, b in enumerate(bins):
        if b == 0: continue
        intensity = min(255, int((i / num_bars) * 510))
        color = (min(255, intensity), min(255, 510 - intensity), 100)
        h = (b / max_bin_height) * (HIST_HEIGHT - int(40*SCALE))
        x = int(60*SCALE) + (i * bar_width)
        y = HIST_Y + HIST_HEIGHT - h
        pygame.draw.rect(screen, color, (x, y, bar_width - 1, h))

def save_simulation(filepath, data):
    try:
        with open(filepath, "wb") as f: pickle.dump(data, f)
        return True, f"SAVED: {os.path.basename(filepath)}"
    except Exception as e:
        return False, f"ERROR: {e}"

def load_simulation(filepath):
    try:
        with open(filepath, "rb") as f: data = pickle.load(f)
        return True, data, f"LOADED: {os.path.basename(filepath)}"
    except Exception as e:
        return False, None, f"ERROR: {e}"

def main():
    pygame.init()
    global KEYS_PER_STAGE # Allow modifying the global
    # Enable key repeat: Delay 300ms, then fire every 50ms
    pygame.key.set_repeat(300, 50)
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(f"Cache Sim: Polished UI (x{SCALE})")
    clock = pygame.time.Clock()
    fonts: Dict[str, Any] = get_fonts()

    num_stages = 1
    def create_stages(n: int, current: Optional[List[Stage]] = None) -> List[Stage]:
        new_stages = []
        spacing = SCREEN_WIDTH / (n + 1)
        for i in range(n):
            # Reuse existing stage if available
            if current and i < len(current):
                stage = current[i]
                # Update position for new layout
                stage.x_trigger = spacing * (i + 1)
                new_stages.append(stage)
            else:
                direction = 1 # Force consistent direction (Below Highway)
                x_pos = spacing * (i + 1)
                new_stages.append(Stage(i, x_pos, direction))
        return new_stages

    stages: List[Stage] = create_stages(num_stages)
    active_stage_idx = 0
    requests: List[Request] = []
    completed_latencies = collections.deque(maxlen=200) 
    
    current_rps = DEFAULT_RPS
    spawn_timer = 0
    active_key_limit = KEYS_PER_STAGE # Default to full range
    sim_time = 0.0      
    sim_speed = 1.0     
    compute_speed = 1.0 
    drag_coeff = 0.005
    
    saturation_enabled = False
    show_help = True
    paused = False

    flash_msg = None
    flash_timer = 0

    # UI Modes: 'sim', 'save_menu', 'load_menu'
    ui_mode = 'sim'
    input_text = ""
    file_list = []
    selected_file_idx = 0
    next_lane_rr = 0 # Round Robin Counter
    active_lane_count = 1 # Default to 1 lane

    running = True
    while running:
        raw_dt = clock.tick(FPS) / 1000.0
        if paused: dt = 0
        else: dt = raw_dt * sim_speed
        sim_time += dt

        if flash_timer > 0:
            flash_timer -= raw_dt
            if flash_timer < 0: flash_msg = None

        # Ensure save directory exists
        if not os.path.exists("saved_states"): os.makedirs("saved_states")
        
        for event in pygame.event.get():
            if event.type == pygame.QUIT: running = False
            elif event.type == pygame.KEYDOWN:
                # --- UI MODE: SAVE MENU ---
                if ui_mode == 'save_menu':
                    if event.key == pygame.K_ESCAPE: 
                        ui_mode = 'sim'; paused = False
                    elif event.key == pygame.K_RETURN:
                        # COMMIT SAVE
                        fname = input_text.strip()
                        if fname:
                            if not fname.endswith(".pkl"): fname += ".pkl"
                            path = os.path.join("saved_states", fname)
                            state = {
                                'stages': stages, 'requests': requests, 'latencies': completed_latencies,
                                'sim_time': sim_time, 'rps': current_rps, 'key_limit': active_key_limit,
                                'drag': drag_coeff, 'comp_spd': compute_speed
                            }
                            success, msg = save_simulation(path, state)
                            flash_msg = msg; flash_timer = 2.0
                        ui_mode = 'sim'; paused = False
                    elif event.key == pygame.K_BACKSPACE:
                        input_text = input_text[:-1]
                    else:
                        # Simple text input filter
                        if event.unicode and event.unicode.isprintable():
                            input_text += event.unicode
                    continue # Skip other controls

                # --- UI MODE: LOAD MENU ---
                if ui_mode == 'load_menu':
                    if event.key == pygame.K_ESCAPE:
                        ui_mode = 'sim'; paused = False
                    elif event.key == pygame.K_UP:
                        selected_file_idx = max(0, selected_file_idx - 1)
                    elif event.key == pygame.K_DOWN:
                        selected_file_idx = min(len(file_list) - 1, selected_file_idx + 1)
                    elif event.key == pygame.K_RETURN:
                        # COMMIT LOAD
                        if file_list:
                            fname = file_list[selected_file_idx]
                            path = os.path.join("saved_states", fname)
                            success, state, msg = load_simulation(path)
                            if success and state is not None:
                                stages = state['stages']; requests = state['requests']
                                completed_latencies = state['latencies']; sim_time = state['sim_time']
                                current_rps = state['rps']; active_key_limit = state['key_limit']
                                drag_coeff = state['drag']; compute_speed = state['comp_spd']
                            flash_msg = msg; flash_timer = 2.0
                        ui_mode = 'sim'; paused = False
                    continue

                # --- UI MODE: SIMULATION ---
                if event.key == pygame.K_ESCAPE: running = False

                mods = pygame.key.get_mods()
                is_shift = mods & pygame.KMOD_SHIFT
                is_ctrl = mods & pygame.KMOD_CTRL

                if event.key == pygame.K_h: show_help = not show_help

                # --- NAMED SAVE/LOAD (Ctrl+S / Ctrl+L) ---
                # --- NAMED SAVE/LOAD (Ctrl+S / Ctrl+L) ---
                if is_ctrl and event.key == pygame.K_s:
                    ui_mode = 'save_menu'
                    input_text = ""
                    paused = True
                    continue
                
                if is_ctrl and event.key == pygame.K_l:
                    ui_mode = 'load_menu'
                    paused = True
                    if os.path.exists("saved_states"):
                        file_list = [f for f in sorted(os.listdir("saved_states")) if f.endswith(".pkl")]
                    else:
                        file_list = []
                    selected_file_idx = 0
                    continue

                # --- SAVE / LOAD (F5 / F6) ---
                if event.key == pygame.K_F5:
                    # Quicksave
                    state = {
                        'stages': stages,
                        'requests': requests,
                        'latencies': completed_latencies,
                        'sim_time': sim_time,
                        'rps': current_rps,
                        'key_limit': active_key_limit,
                        'drag': drag_coeff,
                        'comp_spd': compute_speed
                    }
                    success, msg = save_simulation("sim_state.pkl", state)
                    flash_msg = msg; flash_timer = 2.0

                if event.key == pygame.K_F6:
                    # Quickload
                    if os.path.exists("sim_state.pkl"):
                        success, state, msg = load_simulation("sim_state.pkl")
                        if success and state is not None:
                            stages = state['stages']; requests = state['requests']
                            completed_latencies = state['latencies']; sim_time = state['sim_time']
                            current_rps = state['rps']; active_key_limit = state['key_limit']
                            drag_coeff = state['drag']; compute_speed = state['comp_spd']
                        flash_msg = msg; flash_timer = 2.0
                    else:
                         flash_msg = "NO SAVE FOUND"; flash_timer = 2.0

                
                # --- GLOBAL ---
                if event.key == pygame.K_F3:
                    active_lane_count = 1 if active_lane_count == 2 else 2
                    # Hard Reset on Topology Change
                    requests.clear(); completed_latencies.clear(); 
                    sim_time = 0; spawn_timer = 0; next_lane_rr = 0
                    for s in stages: s.reset()

                if event.key == pygame.K_F1: 
                    if num_stages > 1:
                        num_stages -= 1; stages = create_stages(num_stages, stages)
                        requests.clear(); completed_latencies.clear(); sim_time = 0; active_stage_idx = 0
                if event.key == pygame.K_F2:
                    if num_stages < MAX_STAGES:
                        num_stages += 1; stages = create_stages(num_stages, stages)
                        requests.clear(); completed_latencies.clear(); sim_time = 0; active_stage_idx = 0
                if event.key == pygame.K_SPACE: paused = not paused
                if event.key == pygame.K_UP: current_rps += 2
                if event.key == pygame.K_DOWN: current_rps = max(0, current_rps - 2)
                if event.key == pygame.K_LEFT: sim_speed = max(0.1, round(float(sim_speed - 0.1), 1)) # pyre-ignore[6]
                if event.key == pygame.K_RIGHT: sim_speed = min(5.0, round(float(sim_speed + 0.1), 1)) # pyre-ignore[6]
                if event.key == pygame.K_k: 
                    if is_shift: drag_coeff = max(0.001, round(float(drag_coeff - 0.001), 3)) # pyre-ignore[6]
                    else: compute_speed = max(0.2, round(float(compute_speed - 0.2), 1)) # pyre-ignore[6]
                if event.key == pygame.K_l: 
                    if is_shift: drag_coeff = min(0.100, round(float(drag_coeff + 0.001), 3)) # pyre-ignore[6]
                    else: compute_speed = min(10.0, round(float(compute_speed + 0.2), 1)) # pyre-ignore[6]
                if event.key == pygame.K_s and not is_ctrl: saturation_enabled = not saturation_enabled
                if event.key == pygame.K_r: 
                    requests.clear(); completed_latencies.clear(); 
                    sim_time = 0; spawn_timer = 0;
                    for s in stages: s.reset()
                if event.key == pygame.K_x: completed_latencies.clear()

                # --- STAGE CONTROLS ---
                curr: Stage = stages[active_stage_idx]
                if event.key == pygame.K_TAB:
                    if is_shift: active_stage_idx = (active_stage_idx - 1) % num_stages
                    else: active_stage_idx = (active_stage_idx + 1) % num_stages
                
                if event.key == pygame.K_1: curr.cache_enabled = not curr.cache_enabled
                if event.key == pygame.K_2: curr.refresh_enabled = not curr.refresh_enabled
                if event.key == pygame.K_3: curr.coalesce_enabled = not curr.coalesce_enabled
                if event.key == pygame.K_4: curr.batch_enabled = not curr.batch_enabled
                if event.key == pygame.K_5: curr.l2_enabled = not curr.l2_enabled
                
                if event.key == pygame.K_j:
                     # Cycle Jitter
                     jitters = [0.0, 0.1, 0.2, 0.5, 1.0, 2.0]
                     try:
                         idx = jitters.index(curr.jitter)
                         curr.jitter = jitters[(idx + 1) % len(jitters)]
                     except ValueError:
                         curr.jitter = 0.0
                
                if event.key == pygame.K_i: 
                    for lane in range(MAX_LANES):
                        curr.l1_caches[lane].clear()
                        curr.refresh_active[lane].clear()
                        # curr.leaders_inflight[lane].clear() # REMOVED: Keep waiters waiting for inflight leaders!

                # --- CAPACITY / LATENCY GROUP ([ ]) ---
                if event.key == pygame.K_LEFTBRACKET:
                    if is_shift: curr.work_time = max(0.1, round(float(curr.work_time - 0.1), 1)) # pyre-ignore[6]
                    else: curr.adjust_capacity(-1)
                if event.key == pygame.K_RIGHTBRACKET:
                    if is_shift: curr.work_time = round(float(curr.work_time + 0.1), 1) # pyre-ignore[6]
                    else: curr.adjust_capacity(1)

                # --- BATCH GROUP (- =) ---
                if event.key == pygame.K_MINUS:
                    if is_shift: curr.batch_window = max(0.0, round(float(curr.batch_window - 0.1), 1)) # pyre-ignore[6]
                    else: curr.batch_max_size = max(1, curr.batch_max_size - 1)
                if event.key == pygame.K_EQUALS:
                    if is_shift: curr.batch_window = round(float(curr.batch_window + 0.1), 1) # pyre-ignore[6]
                    else: curr.batch_max_size += 1

                # --- KEYSPACE CONTROL (9 0) ---
                if event.key == pygame.K_9: active_key_limit = max(1, active_key_limit - 1)
                if event.key == pygame.K_0: active_key_limit = min(KEYS_PER_STAGE, active_key_limit + 1)

                # --- TTL / REFRESH GROUP (6 7) ---
                if event.key == pygame.K_6:
                    if is_shift: curr.refresh_time = max(0.1, round(float(curr.refresh_time - 0.1), 1)) # pyre-ignore[6]
                    else: curr.ttl = max(0.5, round(float(curr.ttl - 0.5), 1)) # pyre-ignore[6]
                if event.key == pygame.K_7:
                    if is_shift: curr.refresh_time = round(float(curr.refresh_time + 0.1), 1) # pyre-ignore[6]
                    else: curr.ttl = round(float(curr.ttl + 0.5), 1) # pyre-ignore[6]
                curr.refresh_time = min(curr.refresh_time, curr.ttl)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1: # Left Click
                    # Shift+Click = Buster, Normal Click = Tracer
                    mods = pygame.key.get_mods()
                    is_bust = (mods & pygame.KMOD_SHIFT)
                    requests.append(Request(sim_time, lane_idx=0, is_tracer=True, is_bust=is_bust, key_limit=active_key_limit))
                elif event.button == 3: # Right Click -> Lane 1
                    if active_lane_count > 1:
                        mods = pygame.key.get_mods()
                        is_bust = (mods & pygame.KMOD_SHIFT)
                        requests.append(Request(sim_time, lane_idx=1, is_tracer=True, is_bust=is_bust, key_limit=active_key_limit))

        spawn_timer += dt
        if current_rps > 0 and spawn_timer >= 1.0 / current_rps:
            # Round Robin Spawning
            requests.append(Request(sim_time, lane_idx=next_lane_rr % active_lane_count, key_limit=active_key_limit))
            next_lane_rr = (next_lane_rr + 1) % active_lane_count
            spawn_timer = 0

        # --- CALCULATE DRAG (Saturation) ---
        drag_factor = 1.0
        if saturation_enabled:
            active_count = len(requests)
            drag_factor = 1.0 + (active_count * 0.005)

        # --- UPDATE STAGES ---
        for stage in stages:
            for i in range(MAX_LANES): stage.coalesce_counts[i] = 0
            stage.update(dt, sim_time)

        # --- MOVEMENT ---
        active_requests = [r for r in requests if r.state != 'done']
        requests = active_requests
        
        # Helper to get batch index for visualization
        def get_worker_queue_pos(req, stage):
            for i, batch in enumerate(stage.worker_queue):
                if req in batch: return i, batch.index(req), len(batch)
            return -1, -1, 0

        for req in requests:
            effective_move = (req.base_speed * sim_speed * compute_speed) / drag_factor if not paused else 0
            key = req.get_current_key(num_stages)
            lane = req.lane_idx

            if req.state == 'highway':
                old_x = req.x
                req.x += effective_move
                
                # Lane-specific Y check
                lane_y = HIGHWAY_Y if lane == 0 else HIGHWAY_Y + LANE_SPACING
                
                if req.stage_idx < num_stages:
                    stage = stages[req.stage_idx]
                    if old_x < stage.x_trigger and req.x >= stage.x_trigger:
                        req.x = stage.x_trigger
                        is_fresh = stage.is_cached(lane, key, sim_time)
                        
                        # BUSTER LOGIC: Treat as miss, force refresh
                        force_refresh = req.is_bust

                        should_divert = False
                        if not stage.cache_enabled: should_divert = True 
                        elif is_fresh and not force_refresh: 
                            req.stage_idx += 1 
                            req.is_leader = False # Reset leader status on stage transition 
                            if stage.refresh_enabled:
                                cache = stage.l1_caches[lane]
                                # Refresh if age > refresh_time (calculated as time_remaining < ttl - refresh_time)
                                if (cache[key] - sim_time) < (stage.ttl - stage.refresh_time):
                                     if key not in stage.refresh_active[lane]:
                                         stage.refresh_active[lane][key] = True
                                         ghost = Request(sim_time, lane_idx=lane, is_ghost=True, ghost_key=key, ghost_stage=stage)
                                         requests.append(ghost)
                        elif force_refresh:
                            # It's a buster!
                            # 1. Is a refresh already active?
                            if key in stage.refresh_active[lane]:
                                # Just wait for it
                                req.state = 'waiting_refresh'; req.x = stage.x_trigger
                                stage.waiting_for_refresh[lane][key].append(req)
                            else:
                                # Start one!
                                stage.refresh_active[lane][key] = True
                                ghost = Request(sim_time, lane_idx=lane, is_ghost=True, ghost_key=key, ghost_stage=stage)
                                requests.append(ghost)
                                # Wait for it
                                req.state = 'waiting_refresh'; req.x = stage.x_trigger
                                stage.waiting_for_refresh[lane][key].append(req)
                        else:
                            if stage.coalesce_enabled:
                                if key in stage.leaders_inflight[lane]:
                                    req.state = 'waiting_coalesce'; req.x = stage.x_trigger
                                else:
                                    should_divert = True; stage.leaders_inflight[lane][key] = True; req.is_leader = True
                            else:
                                should_divert = True

                        if should_divert:
                            req.state = 'diverting'; req.stage_idx_ref = stage

                if req.x > SCREEN_WIDTH + int(20 * SCALE):
                    req.state = 'done'
                    completed_latencies.append(sim_time - req.creation_time)

            elif req.state == 'waiting_coalesce':
                stage = stages[req.stage_idx]
                is_fresh = stage.is_cached(lane, key, sim_time)
                if is_fresh: 
                    req.state = 'highway'; req.stage_idx += 1
                    req.is_leader = False
                elif key not in stage.leaders_inflight[lane]: req.state = 'highway'
                else:
                    stage.coalesce_counts[lane] += 1
                    target_x = stage.x_trigger - (stage.coalesce_counts[lane] * int(5 * SCALE))
                    req.x += (target_x - req.x) * 0.2 * sim_speed * compute_speed / drag_factor if not paused else 0

            elif req.state == 'waiting_refresh':
                pass # Just sit there until woken up by the ghost returning

            elif req.state == 'diverting':
                stage = req.stage_idx_ref
                
                lane_dir = 1 if req.lane_idx == 0 else -1
                
                if stage.batch_enabled: 
                    # Pen is near the road
                    # Match draw_scene fixed offsets relative to Rack Top (worker_y_base + 10)
                    rack_y = stage.worker_y_base + int(10*SCALE)

                    if req.lane_idx == 0:
                        # Lane 0 Pen Top is at (rack_y - 90). Center target is +20 inside.
                        target_y = rack_y - int(70 * SCALE)
                    else:
                        # Lane 1 Pen Top is Fixed at (rack_y + 150). Center target is +20 inside.
                        target_y = rack_y + int(170 * SCALE)

                    target_x = stage.x_trigger - int(100 * SCALE)
                else:
                    # Direct to queue area
                    target_y = stage.worker_y_base - (int(25 * SCALE) * lane_dir)
                    target_x = stage.x_trigger + int(20 * SCALE)

                step = 0.15 * sim_speed * compute_speed / drag_factor if not paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step
                
                # Check arrival at Pen
                if abs(target_x - req.x) < 5: 
                    # --- L2 CHECK (The Filter) ---
                    use_l2 = False
                    if stage.l2_enabled and stage.is_l2_cached(key, sim_time):
                        l1_expiry = stage.l1_caches[lane].get(key, 0)
                        l2_expiry = stage.l2_cache[key]
                        # STRICT FRESHNESS: Only use L2 if it is strictly fresher than L1.
                        # This forces Busters/Refreshes to hit the worker if L2 is just as stale as L1.
                        if l2_expiry > l1_expiry:
                            use_l2 = True
                            
                    if use_l2:
                         req.state = 'l2_serving'
                    else:
                        if stage.batch_enabled:
                            req.state = 'in_pen'
                            if len(stage.batch_buffers[lane]) == 0: stage.batch_timers[lane] = sim_time
                            stage.batch_buffers[lane].append(req)
                        else:
                            # BATCHING OFF: Create batch of 1 immediately
                            stage.worker_queue.append([req])
                            req.state = 'queued_for_worker'

            elif req.state == 'in_pen':
                stage = req.stage_idx_ref
                try: idx = stage.batch_buffers[lane].index(req)
                except ValueError: idx = 0
                
                pen_start_x = stage.x_trigger - int(110 * SCALE) + int(5*SCALE)
                
                # Match draw_scene logic for Pen Y
                rack_y = stage.worker_y_base + int(10*SCALE)
                if lane == 0:
                    pen_y_top = rack_y - int(90 * SCALE)
                else: 
                    pen_y_top = rack_y + int(150 * SCALE)

                target_x = pen_start_x + (idx * int(8 * SCALE))
                # Shift DOWN/UP to be inside the box (clearing the label)
                target_y = pen_y_top + int(15 * SCALE) + random.randint(-1, 1)
                step = 0.2 * sim_speed * compute_speed / drag_factor if not paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step

            elif req.state == 'queued_for_worker':
                stage = req.stage_idx_ref
                b_idx, r_idx, b_size = get_worker_queue_pos(req, stage)
                
                # Visual: Stack batches between Pen and Rack
                queue_start_x = stage.x_trigger - int(55 * SCALE) # Right of Pen
                rack_y = stage.worker_y_base + (int(10*SCALE) * stage.y_offset_dir)
                
                # Each batch is a column, items stack vertically
                target_x = queue_start_x + (b_idx * int(10 * SCALE))
                
                # Centered vertically around (rack_y - 35*SCALE)
                base_y = rack_y - int(35 * SCALE)
                total_h = b_size * int(6 * SCALE)
                start_y = base_y - (total_h // 2)
                target_y = start_y + (r_idx * int(6 * SCALE)) + int(3*SCALE) # +3 for radius offset
                
                step = 0.2 * sim_speed * compute_speed / drag_factor if not paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step

            elif req.state == 'l2_serving':
                stage = req.stage_idx_ref
                # Target the specific key slot in L2 grid
                rack_x = stage.x_trigger - RACK_PAD
                l2_w = int(45 * SCALE)
                l2_x = rack_x - l2_w - int(10 * SCALE)
                l2_y = stage.worker_y_base + (int(10*SCALE) * stage.y_offset_dir)
                
                row = key // 2; col = key % 2
                target_x = l2_x + int(10*SCALE) + (col * int(18*SCALE))
                target_y = l2_y + int(16*SCALE) + (row * int(18*SCALE))
                
                step = 0.15 * sim_speed * compute_speed / drag_factor if not paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step
                
                if abs(req.x - target_x) < 5 and abs(req.y - target_y) < 5:
                     # L2 Hit: L1 inherits the remaining validity of the data
                     if key in stage.l2_cache:
                         stage.l1_caches[lane][key] = stage.l2_cache[key]
                     
                     if req.is_leader:
                         if key in stage.leaders_inflight[lane]:
                             del stage.leaders_inflight[lane][key]
                             req.is_leader = False

                     req.state = 'returning'

            elif req.state == 'processing':
                stage = req.stage_idx_ref
                worker_idx = -1
                for i, w in enumerate(stage.workers):
                    if req in w.current_reqs: worker_idx = i; worker = w; break
                
                if worker_idx != -1:
                    blade_pitch = int(10 * SCALE)
                    # Grid Math
                    row = worker_idx // ROW_CAPACITY
                    col = worker_idx % ROW_CAPACITY
                    
                    # SYNC WITH DRAWING CODE:
                    # Rack X start is (trigger - RACK_PAD). Blade start is +5 offset.
                    rack_start_x = stage.x_trigger - RACK_PAD
                    blade_x = rack_start_x + int(5*SCALE) + (col * blade_pitch) + (BLADE_W // 2)
                    
                    # Blade Y
                    blade_y = (stage.worker_y_base + int(10*SCALE)) + (row * int(ROW_V_SPACING*SCALE))
                    
                    step = 0.2 * sim_speed * compute_speed / drag_factor if not paused else 0
                    req.x += (blade_x - req.x) * step
                    # Hover slightly above blade (blade_y is top of blade)
                    target_y = blade_y + int(2*SCALE) 
                    req.y += (target_y - req.y) * step
                    
                    if sim_time >= worker.busy_until:
                        req.state = 'returning'
                        # Worker finished -> Populate L2 (Shared)
                        if stage.l2_enabled:
                            stage.l2_cache[key] = sim_time + stage.get_jittered_ttl()
                        
                        if not req.is_ghost and stage.cache_enabled:
                            # Populate Local L1
                            stage.l1_caches[lane][key] = sim_time + stage.get_jittered_ttl()
                            
                        # ALWAYS clear leader status, even if cache is off or ghost logic weirdness
                        # DO NOT check stage.coalesce_enabled here; if a leader is inflight, it MUST be cleared.
                        if req.is_leader:
                            if key in stage.leaders_inflight[lane]:
                                del stage.leaders_inflight[lane][key]
                                req.is_leader = False

                        if req in worker.current_reqs:
                            worker.current_reqs.remove(req)

                else:
                    # Should not happen in new queue logic, but fail-safe
                    pass

            elif req.state == 'returning':
                stage = req.stage_idx_ref
                step = 0.15 * sim_speed * compute_speed / drag_factor if not paused else 0
                
                if req.is_ghost:
                    lane_dir = 1 if req.lane_idx == 0 else -1
                    lane_y = HIGHWAY_Y if req.lane_idx == 0 else HIGHWAY_Y + LANE_SPACING
                    target_x = stage.x_trigger
                    target_y = lane_y + (int(15 * SCALE) * lane_dir)
                    req.x += (target_x - req.x) * step
                    req.y += (target_y - req.y) * step
                    
                    if abs(req.x - target_x) < 5:
                        stage.l1_caches[lane][key] = sim_time + stage.get_jittered_ttl()
                        if key in stage.refresh_active[lane]: del stage.refresh_active[lane][key]
                        
                        # WAKE UP WAITERS (Busters)
                        if key in stage.waiting_for_refresh[lane]:
                            waiters = stage.waiting_for_refresh[lane].pop(key)
                            for w in waiters:
                                w.state = 'highway'; w.stage_idx += 1
                                w.is_leader = False

                        req.state = 'done'
                else:
                    lane_y = HIGHWAY_Y if req.lane_idx == 0 else HIGHWAY_Y + LANE_SPACING
                    req.x += ((stage.x_trigger + int(80*SCALE)) - req.x) * step
                    req.y += (lane_y - req.y) * step
                    if abs(req.y - lane_y) < 5:
                        req.state = 'highway'; req.y = lane_y + random.randint(int(-8*SCALE), int(8*SCALE)); req.stage_idx += 1
                        req.is_leader = False

        # Drawing
        screen.fill(BG_COLOR)
        pygame.draw.rect(screen, ROAD_COLOR, (0, HIGHWAY_Y - int(25*SCALE), SCREEN_WIDTH, int(50*SCALE)))
        
        if active_lane_count > 1:
            pygame.draw.rect(screen, ROAD_COLOR, (0, HIGHWAY_Y + LANE_SPACING - int(25*SCALE), SCREEN_WIDTH, int(50*SCALE)))
        
        draw_text(screen, fonts['big'], f"Load: {current_rps} Req/s", TEXT_WHITE, (int(30*SCALE), int(20*SCALE)))
        spd_color = GREEN if sim_speed == 1.0 else (ORANGE if paused else RED)
        draw_text(screen, fonts['big'], f"Sim Spd: {sim_speed:.1f}x", spd_color, (int(240*SCALE), int(20*SCALE)))
        c_color = GREEN if compute_speed == 1.0 else ORANGE
        draw_text(screen, fonts['big'], f"Comp Spd: {compute_speed:.1f}x", c_color, (int(450*SCALE), int(20*SCALE)))

        # Saturation Indicator
        sat_color = RED if saturation_enabled else DOT_GRAY
        sat_text = f"Drag: {int((drag_factor-1.0)*100)}% (Sev:{drag_coeff:.3f})" if saturation_enabled else "Drag: OFF"
        draw_text(screen, fonts['big'], sat_text, sat_color, (int(680*SCALE), int(20*SCALE)))

        # --- DRAW KEYSPACE ---
        keyspace_y = HIST_Y - int(80*SCALE)
        draw_text(screen, fonts['tiny'], "KEYSPACE", MUTED, (int(50*SCALE), keyspace_y))
        for k in range(KEYS_PER_STAGE):
            draw_shape(screen, DOT_GRAY, int(50*SCALE) + (k*30), keyspace_y + int(20*SCALE), k, 6)

        # --- DRAW FULL STATUS LEGEND ---
        status_x = int(300*SCALE)
        screen.blit(fonts['tiny'].render("STATUS LEGEND", True, MUTED), (status_x, keyspace_y))
        
        leg_items = [
            (DOT_GRAY, "Travel"), (ORANGE, "Queue/Miss"), (BLUE, "Batch Wait"), (PURPLE, "Coalesce"),
            (RED, "Working"), (GREEN, "Done/Hit"), (WHITE, "Ghost (Hollow)"), (YELLOW, "Refreshing"),
            (CYAN, "L2 Serving")
        ]
        
        for i, (col, txt) in enumerate(leg_items):
            lx = status_x + ((i % 5) * int(120*SCALE))
            ly = keyspace_y + int(20*SCALE) + ((i // 5) * int(25*SCALE)) + int(5*SCALE)
            width = 0 if col != WHITE else 2
            shape = 1 if col == YELLOW else 0
            draw_shape(screen, col, lx + 5, ly + 5, shape, 5, width)
            screen.blit(fonts['tiny'].render(txt, True, MUTED), (lx + 15, ly))


        # --- STAGES ---
        
        for i, stage in enumerate(stages):
            is_active = (i == active_stage_idx)
            
            # HIGHLIGHTING MACHINERY
            structure_color = HIGHLIGHT if is_active else (80, 80, 80)
            pen_border_color = HIGHLIGHT if is_active else PEN_COLOR
            rack_border_color = HIGHLIGHT if is_active else DARK_SERVER
            
            status_parts = []
            if not stage.cache_enabled: status_parts.append("[NO CACHE]")
            else:
                status_parts.append(f"T:{stage.ttl}s")

                if stage.refresh_enabled: status_parts.append(f"R:{stage.refresh_time}s")
                if stage.coalesce_enabled: status_parts.append("[C]")
                if stage.jitter > 0: status_parts.append(f"J:{stage.jitter}s")
            
            status_parts.append(f"L2:{'ON' if stage.l2_enabled else 'OFF'}")
            status_str = " ".join(status_parts)
            work_str = f"Lat: {stage.work_time}s"
            
            if stage.batch_enabled:
                batch_str = f"B: {stage.batch_max_size} / {stage.batch_window}s"
                batch_color = CYAN
            else:
                batch_str = "B: OFF"
                batch_color = DOT_GRAY

            color = HIGHLIGHT if is_active else DOT_GRAY
            # Increased vertical offset to 190 to clear adjacent blades
            # Label goes ABOVE Lane 0
            label_y = HIGHWAY_Y - int(150 * SCALE)
            
            # Shift text left (-60) to center it better
            screen.blit(fonts['title'].render(f"Stage {i+1}", True, color), (stage.x_trigger - int(60*SCALE), label_y))
            draw_text(screen, fonts['std'], work_str, color, (stage.x_trigger - int(60*SCALE), label_y + int(30*SCALE)))
            draw_text(screen, fonts['std'], status_str, color, (stage.x_trigger - int(60*SCALE), label_y + int(50*SCALE)))
            draw_text(screen, fonts['std'], batch_str, batch_color, (stage.x_trigger - int(60*SCALE), label_y + int(70*SCALE)))

            # Draw Connection Lines (Lane 0 Down, Lane 1 Up)
            pygame.draw.line(screen, (40, 40, 50), (stage.x_trigger, HIGHWAY_Y), (stage.x_trigger, stage.worker_y_base), 2)
            if active_lane_count > 1:
                pygame.draw.line(screen, (40, 40, 50), (stage.x_trigger, HIGHWAY_Y + LANE_SPACING), (stage.x_trigger, stage.worker_y_base), 2)
            
            busy_count = sum(1 for w in stage.workers if w.busy_until > sim_time)
            total_cap = len(stage.workers)
            load_pct = busy_count / total_cap if total_cap > 0 else 1.0
            
            # Rack Dimensions Calculation
            rows = (total_cap - 1) // ROW_CAPACITY + 1
            cols = min(total_cap, ROW_CAPACITY)
            
            rack_x = stage.x_trigger - RACK_PAD
            rack_y = stage.worker_y_base + (int(10*SCALE) if stage.y_offset_dir == 1 else -int(10*SCALE))
            rack_w = (ROW_CAPACITY * int(10*SCALE)) + int(10*SCALE)
            rack_h = int(35*SCALE) + ((rows - 1) * int(ROW_V_SPACING*SCALE)) 
            
            pygame.draw.rect(screen, DARK_SERVER, (rack_x - 5, rack_y, rack_w + 10, rack_h), border_radius=4)
            if is_active:
                 pygame.draw.rect(screen, HIGHLIGHT, (rack_x - 5, rack_y, rack_w + 10, rack_h), 2, border_radius=4)

            for b_idx, w in enumerate(stage.workers):
                blade_color = RED if w.busy_until > sim_time else BLUE
                row = b_idx // ROW_CAPACITY
                col = b_idx % ROW_CAPACITY
                bx = rack_x + int(5*SCALE) + (col * int(10*SCALE))
                by = rack_y + int(5*SCALE) + (row * int(ROW_V_SPACING*SCALE))
                pygame.draw.rect(screen, blade_color, (bx, by, BLADE_W, BLADE_H), border_radius=2)

            # --- LOAD TIMELINE GRAPH ---
            graph_h = int(25*SCALE)
            graph_y = rack_y + rack_h + int(5*SCALE)
            graph_rect = (rack_x - 5, graph_y, rack_w + 10, graph_h)
            
            # Background
            pygame.draw.rect(screen, (20, 20, 25), graph_rect)
            pygame.draw.rect(screen, (60, 60, 70), graph_rect, 1)

            # Plot History
            if len(stage.load_history) > 1:
                pts = []
                w_step = (rack_w + 10) / 100 # Max history len
                for h_idx, val in enumerate(stage.load_history):
                    lx = (rack_x - 5) + (h_idx * w_step)
                    ly = graph_y + graph_h - (val * graph_h)
                    pts.append((lx, ly))
                
                # Close the polygon for fill
                poly_pts = [(rack_x - 5, graph_y + graph_h)] + pts + [(pts[-1][0], graph_y + graph_h)]
                
                # Color based on current load (or avg?) - Use current for consistent flair
                graph_color = GREEN if load_pct < 0.5 else (ORANGE if load_pct < 0.8 else RED)
                # Draw Fill (with transparency if possible? No, standard pygame is easier opaque)
                pygame.draw.polygon(screen, graph_color, poly_pts)
                # Draw Line
                pygame.draw.lines(screen, WHITE, False, pts, 1)

            # Current Level Indicator (Right Side Bar)
            bar_bh = int(load_pct * graph_h)
            pygame.draw.rect(screen, WHITE, (rack_x + rack_w + 2, graph_y + graph_h - bar_bh, int(3*SCALE), bar_bh))

            # Stats Label
            avg_load = sum(stage.load_history)/len(stage.load_history) if stage.load_history else 0.0
            metric_str = f"Load:{int(load_pct*100)}% Avg:{int(avg_load*100)}%"
            screen.blit(fonts['tiny'].render(metric_str, True, DOT_GRAY, BG_COLOR), (rack_x, graph_y + graph_h + int(2*SCALE)))

            # --- PER-LANE INFRASTRUCTURE LOOP ---
            for lane in range(active_lane_count):
                lane_dir = 1 if lane == 0 else -1
                lane_base_y = HIGHWAY_Y if lane == 0 else HIGHWAY_Y + LANE_SPACING
                
                # GANTRY
                g_top = lane_base_y - int(60*SCALE)
                g_bot = lane_base_y + int(60*SCALE)
                
                # Main vertical line
                pygame.draw.line(screen, structure_color, (stage.x_trigger, g_top), (stage.x_trigger, g_bot), 4)
                
                # Supports
                off_x = int(20 * SCALE); off_y = int(25 * SCALE)
                pygame.draw.line(screen, (60, 60, 60), (stage.x_trigger - off_x, g_top + off_y), (stage.x_trigger, g_top), 3)
                pygame.draw.line(screen, (60, 60, 60), (stage.x_trigger - off_x, g_bot - off_y), (stage.x_trigger, g_bot), 3)

                # Keys on Gantry
                for k in range(KEYS_PER_STAGE):
                    k_cached = stage.is_cached(lane, k, sim_time)
                    k_refresh = k in stage.refresh_active[lane]
                    if k_cached: k_color = GREEN
                    elif k_refresh: k_color = YELLOW
                    else: k_color = (60, 60, 60)
                    
                    # Distribute keys vertically along gantry
                    ky = (lane_base_y - int(60*SCALE)) + int(10*SCALE) + (k * int(20*SCALE))
                    
                    if k >= active_key_limit:
                        pygame.draw.line(screen, (50, 20, 20), (stage.x_trigger-5, ky-5), (stage.x_trigger+5, ky+5), 2)
                        pygame.draw.line(screen, (50, 20, 20), (stage.x_trigger+5, ky-5), (stage.x_trigger-5, ky+5), 2)
                        k_color = (30, 30, 30)

                    draw_shape(screen, k_color, stage.x_trigger, ky, k, int(6*SCALE))

                    # Decay Meter
                    cache = stage.l1_caches[lane]
                    if k in cache:
                        expiry = cache[k]
                        remaining = expiry - sim_time
                        if remaining > 0:
                            pct = max(0.0, min(1.0, remaining / stage.ttl))
                            stale_threshold = stage.ttl - stage.refresh_time
                            is_stale = remaining < stale_threshold
                            bar_color = GREEN if not is_stale else YELLOW
                            bar_h = int(14 * SCALE)
                            curr_h = int(bar_h * pct)
                            bx = stage.x_trigger + int(12 * SCALE)
                            by = ky - int(7 * SCALE)
                            pygame.draw.rect(screen, (30, 30, 30), (bx, by, int(3*SCALE), bar_h))
                            fill_y = by + (bar_h - curr_h)
                            pygame.draw.rect(screen, bar_color, (bx, fill_y, int(3*SCALE), curr_h))
                            if stage.ttl > 0:
                                th_pct = stale_threshold / stage.ttl
                                th_y = by + (bar_h - int(bar_h * th_pct))
                                pygame.draw.line(screen, (100, 100, 100), (bx-1, th_y), (bx+int(4*SCALE), th_y), 1)

                # PEN
                if lane == 0:
                    pen_y = rack_y - int(90 * SCALE)
                else:
                    pen_y = rack_y + int(130 * SCALE)

                pen_x = stage.x_trigger - int(110 * SCALE)
                pen_h = int(40 * SCALE) 
                pygame.draw.rect(screen, pen_border_color, (pen_x, pen_y, int(40*SCALE), pen_h), 2)
                draw_text(screen, fonts['tiny'], "Pen", MUTED, (pen_x+5, pen_y-12))

                # WORKER QUEUE (Relative to Pen)
                # Only draw Worker Queue for Lane 0 (it represents the shared queue)
                if lane == 0:
                    bq_x = pen_x + int(45*SCALE)
                    for i, batch in enumerate(stage.worker_queue):
                        bx = bq_x + (i * int(10*SCALE))
                        b_h = len(batch) * int(6*SCALE) + 4
                        base_y = rack_y - int(35 * SCALE)
                        pygame.draw.rect(screen, (50, 50, 60), (bx-2, base_y - (b_h//2), int(8*SCALE), b_h), 1)
            
            # --- DRAW L2 CACHE (Vertical Orientation) ---
            l2_w = int(45 * SCALE) 
            l2_h = int(85 * SCALE) 
            l2_x = rack_x - l2_w - int(10 * SCALE) # Close gap to Rack (10 padding)
            l2_y = rack_y
            
            l2_border_col = (50, 45, 60) if stage.l2_enabled else (80, 30, 30)
            l2_label = "L2" if stage.l2_enabled else "L2 (OFF)"
            
            # Draw Container
            pygame.draw.rect(screen, (30, 25, 40), (l2_x, l2_y, l2_w, l2_h), border_radius=4)
            pygame.draw.rect(screen, l2_border_col, (l2_x, l2_y, l2_w, l2_h), 2, border_radius=4)
            screen.blit(fonts['tiny'].render(l2_label, True, MUTED), (l2_x, l2_y - 12))

            # Draw Empty Grid Slots (Visual Placeholder)
            for k in range(KEYS_PER_STAGE):
                is_l2 = stage.l2_enabled and stage.is_l2_cached(k, sim_time)
                color = GREEN if is_l2 else (40, 35, 50)
                row = k // 2; col = k % 2
                # User offsets: 10, 16
                sx = l2_x + int(10*SCALE) + (col * int(18 * SCALE)) 
                sy = l2_y + int(16*SCALE) + (row * int(18 * SCALE))
                draw_shape(screen, color, sx, sy, k, int(5*SCALE)) 
                
                # --- L2 DECAY METER ---
                if stage.l2_enabled and k in stage.l2_cache:
                    expiry = stage.l2_cache[k]
                    remaining = expiry - sim_time
                    if remaining > 0:
                        pct = max(0.0, min(1.0, remaining / stage.ttl))
                        
                        # Recycle logic for stale color
                        stale_threshold = stage.ttl - stage.refresh_time
                        is_stale = remaining < stale_threshold
                        bar_color = GREEN if not is_stale else YELLOW
                        
                        bar_h = int(10 * SCALE) # Match dot height
                        curr_h = int(bar_h * pct)
                        
                        # Draw to the right of the dot
                        bx = sx + int(7 * SCALE)
                        by = sy - int(5 * SCALE)
                        
                        # Background
                        pygame.draw.rect(screen, (30, 30, 30), (bx, by, int(3*SCALE), bar_h))
                        # Fill
                        pygame.draw.rect(screen, bar_color, (bx, by + (bar_h - curr_h), int(3*SCALE), curr_h))

        for req in requests: 
            c = get_state_color(req)
            width = 2 if req.is_ghost else 0
            key = req.get_current_key(num_stages) 
            draw_shape(screen, c, int(req.x), int(req.y), key, DOT_RADIUS, width)

            if req.is_bust:
                # Cyan Ring for Busters
                pygame.draw.circle(screen, CYAN_BUST, (int(req.x), int(req.y)), int(8*SCALE), 2)

            if req.is_tracer:
                # Draw reticle
                tx, ty = int(req.x), int(req.y)
                pygame.draw.circle(screen, WHITE, (tx, ty), int(10*SCALE), 1)
                pygame.draw.line(screen, WHITE, (tx - int(14*SCALE), ty), (tx + int(14*SCALE), ty), 1)
                pygame.draw.line(screen, WHITE, (tx, ty - int(14*SCALE)), (tx, ty + int(14*SCALE)), 1)

            
        draw_histogram(screen, completed_latencies, fonts)
        
        # --- DRAW HELP PANEL ---
        if show_help:
            p_w = int(520*SCALE)
            p_h = int(520*SCALE)
            p_x = int(30*SCALE)
            p_y = int(60*SCALE)
            s = pygame.Surface((p_w, p_h), pygame.SRCALPHA)
            s.fill(PANEL_BG)
            screen.blit(s, (p_x, p_y))
            
            def draw_row(label, keys_str, y_off):
                draw_text(screen, fonts['std'], label, TEXT_WHITE, (p_x + int(20*SCALE), p_y + y_off))
                x_cursor = p_x + int(250*SCALE)
                for k in keys_str:
                    x_cursor = draw_key_glyph(screen, fonts, k, x_cursor, p_y + y_off - 5)

            draw_text(screen, fonts['title'], "CONTROLS (Press H to Hide)", HIGHLIGHT, (p_x + int(20*SCALE), p_y + int(20*SCALE)))
            
            draw_row("Global Load / Speed", ["UP", "DN", "LF", "RT"], int(60*SCALE))

            draw_row("Comp (Shft:Drag) / Sat", ["K", "L", "S"], int(100*SCALE))
            draw_row("Stage / Reset / Hist", ["TAB", "R", "X"], int(140*SCALE))
            draw_row("Toggle Features", ["1", "2", "3", "4", "5", "J"], int(180*SCALE))
            
            pygame.draw.line(screen, DOT_GRAY, (p_x+20, p_y+int(220*SCALE)), (p_x+p_w-20, p_y+int(220*SCALE)), 1)
            
            draw_text(screen, fonts['std'], "Stage Tuning (Hold SHIFT for Time)", YELLOW, (p_x + int(20*SCALE), p_y + int(240*SCALE)))
            
            draw_row("Workers / Latency", ["[", "]"], int(270*SCALE))
            draw_row("Batch Size / Window", ["-", "="], int(310*SCALE))
            draw_row("TTL / Refresh", ["6", "7"], int(350*SCALE))
            draw_row("Usable Keyspace", ["9", "0"], int(390*SCALE))
            draw_row("Save / Load (Menu)", ["^S", "^L"], int(430*SCALE))
            draw_row("Snapshot (Quick)", ["F5", "F6"], int(470*SCALE))

        else:
             screen.blit(fonts['std'].render("[H] Controls", True, MUTED), (int(30*SCALE), int(80*SCALE)))

        # --- MODAL OVERLAYS ---
        if ui_mode == 'save_menu':
            # Draw Modal Overlay
            s = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            s.fill((0, 0, 0, 180))
            screen.blit(s, (0, 0))
            
            box_w, box_h = int(400*SCALE), int(150*SCALE)
            box_x, box_y = (SCREEN_WIDTH - box_w)//2, (SCREEN_HEIGHT - box_h)//2
            pygame.draw.rect(screen, PANEL_BG, (box_x, box_y, box_w, box_h))
            pygame.draw.rect(screen, HIGHLIGHT, (box_x, box_y, box_w, box_h), 2)
            
            draw_text(screen, fonts['title'], "SAVE SIMULATION", TEXT_WHITE, (box_x + 20, box_y + 20))
            draw_text(screen, fonts['std'], "Filename:", TEXT_WHITE, (box_x + 20, box_y + 60))
            
            # Input Box
            pygame.draw.rect(screen, BLACK, (box_x + 100, box_y + 55, box_w - 120, 30))
            draw_text(screen, fonts['std'], input_text + "|", TEXT_WHITE, (box_x + 105, box_y + 60))
            
            draw_text(screen, fonts['small'], "Press ENTER to Save, ESC to Cancel", DIM_GRAY, (box_x + 20, box_y + 110))

        if ui_mode == 'load_menu':
             # Draw Box
            box_w, box_h = int(400*SCALE), int(300*SCALE)
            box_x, box_y = (SCREEN_WIDTH - box_w)//2, (SCREEN_HEIGHT - box_h)//2
            pygame.draw.rect(screen, (40, 40, 50), (box_x, box_y, box_w, box_h), border_radius=10)
            pygame.draw.rect(screen, HIGHLIGHT, (box_x, box_y, box_w, box_h), 2, border_radius=10)
            
            draw_text(screen, fonts['title'], "LOAD STATE", YELLOW, (box_x + 20, box_y + 20))
            
            # Draw File List
            list_y = box_y + 80
            for i, fname in enumerate(file_list):
                color = HIGHLIGHT if i == selected_file_idx else DOT_GRAY
                if list_y < box_y + box_h - 30:
                    draw_text(screen, fonts['std'], fname, color, (box_x + 30, list_y))
                    list_y += int(20*SCALE)
            
            if not file_list:
                draw_text(screen, fonts['std'], "(No saves found)", MUTED, (box_x + 30, list_y))

        # --- FLASH MESSAGE ---
        if flash_msg:
            msg_surf = fonts['big'].render(flash_msg, True, YELLOW, (20, 20, 20))
            screen.blit(msg_surf, (SCREEN_WIDTH // 2 - msg_surf.get_width() // 2, SCREEN_HEIGHT // 2))

        pygame.display.flip()

    pygame.quit()

if __name__ == "__main__":
    main()
