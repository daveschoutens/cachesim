import random
import collections
from typing import List, Dict, Optional, Deque, Any, Tuple, cast
from config import *

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
