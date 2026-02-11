import pygame
from typing import Dict, Any, Tuple
from config import *
from models import Request, Stage

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

def draw_sim(screen, sim, fonts, show_help, ui_mode, input_text, file_list, selected_file_idx, flash_msg, active_stage_idx):
    screen.fill(BG_COLOR)
    pygame.draw.rect(screen, ROAD_COLOR, (0, HIGHWAY_Y - int(25*SCALE), SCREEN_WIDTH, int(50*SCALE)))
    
    if sim.active_lane_count > 1:
        pygame.draw.rect(screen, ROAD_COLOR, (0, HIGHWAY_Y + LANE_SPACING - int(25*SCALE), SCREEN_WIDTH, int(50*SCALE)))
    
    draw_text(screen, fonts['big'], f"Load: {sim.current_rps} Req/s", TEXT_WHITE, (int(30*SCALE), int(20*SCALE)))
    spd_color = GREEN if sim.sim_speed == 1.0 else (ORANGE if sim.paused else RED)
    draw_text(screen, fonts['big'], f"Sim Spd: {sim.sim_speed:.1f}x", spd_color, (int(240*SCALE), int(20*SCALE)))
    c_color = GREEN if sim.compute_speed == 1.0 else ORANGE
    draw_text(screen, fonts['big'], f"Comp Spd: {sim.compute_speed:.1f}x", c_color, (int(450*SCALE), int(20*SCALE)))

    # Saturation Indicator
    sat_color = RED if sim.saturation_enabled else DOT_GRAY
    active_count = len(sim.requests)
    current_drag_factor = 1.0 + (active_count * 0.005) if sim.saturation_enabled else 1.0
    sat_text = f"Drag: {int((current_drag_factor-1.0)*100)}% (Sev:{sim.drag_coeff:.3f})" if sim.saturation_enabled else "Drag: OFF"
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
    
    for i, stage in enumerate(sim.stages):
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
        if sim.active_lane_count > 1:
            pygame.draw.line(screen, (40, 40, 50), (stage.x_trigger, HIGHWAY_Y + LANE_SPACING), (stage.x_trigger, stage.worker_y_base), 2)
        
        busy_count = sum(1 for w in stage.workers if w.busy_until > sim.sim_time)
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
            blade_color = RED if w.busy_until > sim.sim_time else BLUE
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
        for lane in range(sim.active_lane_count):
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
                k_cached = stage.is_cached(lane, k, sim.sim_time)
                k_refresh = k in stage.refresh_active[lane]
                if k_cached: k_color = GREEN
                elif k_refresh: k_color = YELLOW
                else: k_color = (60, 60, 60)
                
                # Distribute keys vertically along gantry
                ky = (lane_base_y - int(60*SCALE)) + int(10*SCALE) + (k * int(20*SCALE))
                
                if k >= sim.active_key_limit:
                    pygame.draw.line(screen, (50, 20, 20), (stage.x_trigger-5, ky-5), (stage.x_trigger+5, ky+5), 2)
                    pygame.draw.line(screen, (50, 20, 20), (stage.x_trigger+5, ky-5), (stage.x_trigger-5, ky+5), 2)
                    k_color = (30, 30, 30)

                draw_shape(screen, k_color, stage.x_trigger, ky, k, int(6*SCALE))

                # Decay Meter
                cache = stage.l1_caches[lane]
                if k in cache:
                    expiry = cache[k]
                    remaining = expiry - sim.sim_time
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
            is_l2 = stage.l2_enabled and stage.is_l2_cached(k, sim.sim_time)
            color = GREEN if is_l2 else (40, 35, 50)
            row = k // 2; col = k % 2
            # User offsets: 10, 16
            sx = l2_x + int(10*SCALE) + (col * int(18 * SCALE)) 
            sy = l2_y + int(16*SCALE) + (row * int(18 * SCALE))
            draw_shape(screen, color, sx, sy, k, int(5*SCALE)) 
            
            # --- L2 DECAY METER ---
            if stage.l2_enabled and k in stage.l2_cache:
                expiry = stage.l2_cache[k]
                remaining = expiry - sim.sim_time
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

    for req in sim.requests: 
        c = get_state_color(req)
        width = 2 if req.is_ghost else 0
        key = req.get_current_key(sim.num_stages) 
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

        
    draw_histogram(screen, sim.completed_latencies, fonts)
    
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
