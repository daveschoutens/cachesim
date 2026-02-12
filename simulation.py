import collections
from typing import List, Dict, Optional, Deque, Any, Tuple
from config import *
from models import Stage, Request
from random import randint

class Simulation:
    def __init__(self):
        self.num_stages = 1
        self.stages: List[Stage] = []
        self._create_stages(self.num_stages)
        
        self.requests: List[Request] = []
        self.completed_latencies: Deque[float] = collections.deque(maxlen=200)
        
        self.current_rps = DEFAULT_RPS
        self.spawn_timer = 0
        self.active_key_limit = KEYS_PER_STAGE
        
        self.sim_time = 0.0
        self.sim_speed = 1.0
        self.compute_speed = 1.0
        self.drag_coeff = 0.005
        
        self.saturation_enabled = False
        self.paused = False
        
        self.active_lane_count = 1
        self.next_lane_rr = 0
        
    def _create_stages(self, n: int):
        new_stages = []
        spacing = SCREEN_WIDTH / (n + 1)
        for i in range(n):
            if i < len(self.stages):
                stage = self.stages[i]
                stage.x_trigger = int(spacing * (i + 1))
                new_stages.append(stage)
            else:
                direction = 1
                x_pos = int(spacing * (i + 1))
                new_stages.append(Stage(i, x_pos, direction))
        self.stages = new_stages
        self.num_stages = n
        
    def reset(self):
        self.requests.clear()
        self.completed_latencies.clear()
        self.sim_time = 0
        self.spawn_timer = 0
        for s in self.stages: s.reset()

    def get_state(self):
        stage_stats = []
        for s in self.stages:
            stage_stats.append({
                'idx': s.index,
                'ttl': s.ttl,
                'workers': len(s.workers),
                'work_time': s.work_time,
                'jitter': s.jitter,
                'batch_enabled': s.batch_enabled,
                'batch_window': s.batch_window,
                'batch_max_size': s.batch_max_size,
                'coalesce_enabled': s.coalesce_enabled,
                'load': sum(s.load_history)/len(s.load_history) if s.load_history else 0.0,
                'l2_enabled': s.l2_enabled,
                'refresh_count': len(s.refresh_active[0]) + (len(s.refresh_active[1]) if self.active_lane_count > 1 else 0)
            })
        
        return {
            "type": "STATE",
            "sim_time": self.sim_time,
            "rps": self.current_rps,
            "sim_speed": self.sim_speed,
            "compute_speed": self.compute_speed,
            "drag": self.drag_coeff,
            "avg_latency": sum(self.completed_latencies)/len(self.completed_latencies) if self.completed_latencies else 0.0,
            "active_requests": len(self.requests),
            "stages": stage_stats,
            "paused": self.paused
        }

    def handle_action(self, action, cmd):
        if action == 'set_rps':
            self.current_rps = int(cmd.get('value', self.current_rps))
        elif action == 'set_speed':
            self.sim_speed = float(cmd.get('value', self.sim_speed))
        elif action == 'toggle_pause':
            self.paused = not self.paused
        elif action == 'add_stage':
             if self.num_stages < MAX_STAGES:
                self._create_stages(self.num_stages + 1)
                self.reset()
        elif action == 'configure_stage':
            idx = int(cmd.get('stage_idx', 0))
            if 0 <= idx < len(self.stages):
                s = self.stages[idx]
                if 'ttl' in cmd: s.ttl = float(cmd['ttl'])
                if 'work_time' in cmd: s.work_time = float(cmd['work_time'])
                if 'jitter' in cmd: s.jitter = float(cmd['jitter'])
                if 'cache_enabled' in cmd: s.cache_enabled = bool(cmd['cache_enabled'])
                if 'refresh_enabled' in cmd: s.refresh_enabled = bool(cmd['refresh_enabled'])
                if 'l2_enabled' in cmd: s.l2_enabled = bool(cmd['l2_enabled'])
                if 'batch_enabled' in cmd: s.batch_enabled = bool(cmd['batch_enabled'])
                if 'batch_window' in cmd: s.batch_window = float(cmd['batch_window'])
                if 'batch_max_size' in cmd: s.batch_max_size = int(cmd['batch_max_size'])
                if 'coalesce_enabled' in cmd: s.coalesce_enabled = bool(cmd['coalesce_enabled'])
                if 'workers' in cmd:
                    new_count = max(1, min(MAX_WORKERS, int(cmd['workers'])))
                    from models import BackendWorker # Local import to avoid circular dependency in models if it were there (it's not but safe)
                    s.workers = [BackendWorker() for _ in range(new_count)]
        elif action == 'invalidate_cache':
            idx = int(cmd.get('stage_idx', 0))
            if 0 <= idx < len(self.stages):
                s = self.stages[idx]
                key = cmd.get('key')
                if key is not None:
                    k = int(key)
                    for lane in range(MAX_LANES):
                        if k in s.l1_caches[lane]: del s.l1_caches[lane][k]
                    if k in s.l2_cache: del s.l2_cache[k]
                else:
                    for lane in range(MAX_LANES): s.l1_caches[lane].clear()
                    s.l2_cache.clear()
        elif action == 'set_global':
            if 'drag' in cmd: self.drag_coeff = float(cmd['drag'])
            if 'compute_speed' in cmd: self.compute_speed = float(cmd['compute_speed'])
        elif action == 'reset':
             self.reset()

    def update(self, raw_dt):
        if self.paused: dt = 0
        else: dt = raw_dt * self.sim_speed
        self.sim_time += dt

        # Spawn
        self.spawn_timer += dt
        if self.current_rps > 0 and self.spawn_timer >= 1.0 / self.current_rps:
            self.requests.append(Request(self.sim_time, lane_idx=self.next_lane_rr % self.active_lane_count, key_limit=self.active_key_limit))
            self.next_lane_rr = (self.next_lane_rr + 1) % self.active_lane_count
            self.spawn_timer = 0

        # Drag
        drag_factor = 1.0
        if self.saturation_enabled:
            active_count = len(self.requests)
            drag_factor = 1.0 + (active_count * 0.005)

        # Update Stages
        for stage in self.stages:
            for i in range(MAX_LANES): stage.coalesce_counts[i] = 0
            stage.update(dt, self.sim_time)

        # Update Requests
        active_requests = [r for r in self.requests if r.state != 'done']
        self.requests = active_requests

        # Helper
        def get_worker_queue_pos(req, stage):
            for i, batch in enumerate(stage.worker_queue):
                if req in batch: return i, batch.index(req), len(batch)
            return -1, -1, 0

        for req in self.requests:
            effective_move = (req.base_speed * self.sim_speed * self.compute_speed) / drag_factor if not self.paused else 0
            key = req.get_current_key(self.num_stages)
            lane = req.lane_idx

            if req.state == 'highway':
                old_x = req.x
                req.x += effective_move
                
                if req.stage_idx < self.num_stages:
                    stage = self.stages[req.stage_idx]
                    if old_x < stage.x_trigger and req.x >= stage.x_trigger:
                        req.x = stage.x_trigger
                        is_fresh = stage.is_cached(lane, key, self.sim_time)
                        force_refresh = req.is_bust

                        should_divert = False
                        if not stage.cache_enabled: should_divert = True 
                        elif is_fresh and not force_refresh: 
                            req.stage_idx += 1 
                            req.is_leader = False 
                            if stage.refresh_enabled:
                                cache = stage.l1_caches[lane]
                                if (cache[key] - self.sim_time) < (stage.ttl - stage.refresh_time):
                                     if key not in stage.refresh_active[lane]:
                                         stage.refresh_active[lane][key] = True
                                         ghost = Request(self.sim_time, lane_idx=lane, is_ghost=True, ghost_key=key, ghost_stage=stage)
                                         self.requests.append(ghost)
                        elif force_refresh:
                            if key in stage.refresh_active[lane]:
                                req.state = 'waiting_refresh'; req.x = stage.x_trigger
                                stage.waiting_for_refresh[lane][key].append(req)
                            else:
                                stage.refresh_active[lane][key] = True
                                ghost = Request(self.sim_time, lane_idx=lane, is_ghost=True, ghost_key=key, ghost_stage=stage)
                                self.requests.append(ghost)
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
                    self.completed_latencies.append(self.sim_time - req.creation_time)

            elif req.state == 'waiting_coalesce':
                stage = self.stages[req.stage_idx]
                is_fresh = stage.is_cached(lane, key, self.sim_time)
                if is_fresh: 
                    req.state = 'highway'; req.stage_idx += 1
                    req.is_leader = False
                elif key not in stage.leaders_inflight[lane]: req.state = 'highway'
                else:
                    stage.coalesce_counts[lane] += 1
                    target_x = stage.x_trigger - (stage.coalesce_counts[lane] * int(5 * SCALE))
                    req.x += (target_x - req.x) * 0.2 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0

            elif req.state == 'waiting_refresh':
                pass 

            elif req.state == 'diverting':
                stage = req.stage_idx_ref
                lane_dir = 1 if req.lane_idx == 0 else -1
                
                if stage.batch_enabled: 
                    rack_y = stage.worker_y_base + int(10*SCALE)
                    if req.lane_idx == 0:
                        target_y = rack_y - int(70 * SCALE)
                    else:
                        target_y = rack_y + int(170 * SCALE)
                    target_x = stage.x_trigger - int(100 * SCALE)
                else:
                    target_y = stage.worker_y_base - (int(25 * SCALE) * lane_dir)
                    target_x = stage.x_trigger + int(20 * SCALE)

                step = 0.15 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step
                
                if abs(target_x - req.x) < 5: 
                    use_l2 = False
                    if stage.l2_enabled and stage.is_l2_cached(key, self.sim_time):
                        l1_expiry = stage.l1_caches[lane].get(key, 0)
                        l2_expiry = stage.l2_cache[key]
                        if l2_expiry > l1_expiry:
                            use_l2 = True
                            
                    if use_l2:
                         req.state = 'l2_serving'
                    else:
                        if stage.batch_enabled:
                            req.state = 'in_pen'
                            if len(stage.batch_buffers[lane]) == 0: stage.batch_timers[lane] = self.sim_time
                            stage.batch_buffers[lane].append(req)
                        else:
                            stage.worker_queue.append([req])
                            req.state = 'queued_for_worker'

            elif req.state == 'in_pen':
                stage = req.stage_idx_ref
                try: idx = stage.batch_buffers[lane].index(req)
                except ValueError: idx = 0
                
                pen_start_x = stage.x_trigger - int(110 * SCALE) + int(5*SCALE)
                rack_y = stage.worker_y_base + int(10*SCALE)
                if lane == 0:
                    pen_y_top = rack_y - int(90 * SCALE)
                else: 
                    pen_y_top = rack_y + int(150 * SCALE)

                target_x = pen_start_x + (idx * int(8 * SCALE))
                target_y = pen_y_top + int(15 * SCALE) + randint(-1, 1)

                step = 0.2 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step

            elif req.state == 'queued_for_worker':
                stage = req.stage_idx_ref
                b_idx, r_idx, b_size = get_worker_queue_pos(req, stage)
                
                queue_start_x = stage.x_trigger - int(55 * SCALE) 
                rack_y = stage.worker_y_base + (int(10*SCALE) * stage.y_offset_dir)
                target_x = queue_start_x + (b_idx * int(10 * SCALE))
                base_y = rack_y - int(35 * SCALE)
                total_h = b_size * int(6 * SCALE)
                start_y = base_y - (total_h // 2)
                target_y = start_y + (r_idx * int(6 * SCALE)) + int(3*SCALE)
                
                step = 0.2 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step

            elif req.state == 'l2_serving':
                stage = req.stage_idx_ref
                rack_x = stage.x_trigger - RACK_PAD
                l2_w = int(45 * SCALE)
                l2_x = rack_x - l2_w - int(10 * SCALE)
                l2_y = stage.worker_y_base + (int(10*SCALE) * stage.y_offset_dir)
                
                row = key // 2; col = key % 2
                target_x = l2_x + int(10*SCALE) + (col * int(18*SCALE))
                target_y = l2_y + int(16*SCALE) + (row * int(18*SCALE))
                
                step = 0.15 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0
                req.x += (target_x - req.x) * step
                req.y += (target_y - req.y) * step
                
                if abs(req.x - target_x) < 5 and abs(req.y - target_y) < 5:
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
                    row = worker_idx // ROW_CAPACITY
                    col = worker_idx % ROW_CAPACITY
                    
                    rack_start_x = stage.x_trigger - RACK_PAD
                    blade_x = rack_start_x + int(5*SCALE) + (col * blade_pitch) + (BLADE_W // 2)
                    blade_y = (stage.worker_y_base + int(10*SCALE)) + (row * int(ROW_V_SPACING*SCALE))
                    
                    step = 0.2 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0
                    req.x += (blade_x - req.x) * step
                    target_y = blade_y + int(2*SCALE) 
                    req.y += (target_y - req.y) * step
                    
                    if self.sim_time >= worker.busy_until:
                        req.state = 'returning'
                        if stage.l2_enabled:
                            stage.l2_cache[key] = self.sim_time + stage.get_jittered_ttl()
                        
                        if not req.is_ghost and stage.cache_enabled:
                            stage.l1_caches[lane][key] = self.sim_time + stage.get_jittered_ttl()
                            
                        if req.is_leader:
                            if key in stage.leaders_inflight[lane]:
                                del stage.leaders_inflight[lane][key]
                                req.is_leader = False

                        if req in worker.current_reqs:
                            worker.current_reqs.remove(req)

            elif req.state == 'returning':
                stage = req.stage_idx_ref
                step = 0.15 * self.sim_speed * self.compute_speed / drag_factor if not self.paused else 0
                
                if req.is_ghost:
                    lane_dir = 1 if req.lane_idx == 0 else -1
                    lane_y = HIGHWAY_Y if req.lane_idx == 0 else HIGHWAY_Y + LANE_SPACING
                    target_x = stage.x_trigger
                    target_y = lane_y + (int(15 * SCALE) * lane_dir)
                    req.x += (target_x - req.x) * step
                    req.y += (target_y - req.y) * step
                    
                    if abs(req.x - target_x) < 5:
                        stage.l1_caches[lane][key] = self.sim_time + stage.get_jittered_ttl()
                        if key in stage.refresh_active[lane]: del stage.refresh_active[lane][key]
                        
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
                        req.state = 'highway'; req.y = lane_y + randint(int(-8*SCALE), int(8*SCALE)); req.stage_idx += 1
                        req.is_leader = False
