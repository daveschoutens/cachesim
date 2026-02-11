import pygame
import os
import time
import pickle
from config import *
from simulation import Simulation
from agent import AgentInterface
from renderer import draw_sim, get_fonts
from models import Request

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
    # global KEYS_PER_STAGE # Removed global keyword, using config constant directly
    pygame.key.set_repeat(300, 50)
    screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT))
    pygame.display.set_caption(f"Cache Sim: Modular (x{SCALE})")
    clock = pygame.time.Clock()
    fonts = get_fonts()

    # Initialize Simulation and Agent
    sim = Simulation()
    agent = AgentInterface(sim)

    # UI State
    show_help = True
    ui_mode = 'sim' # 'sim', 'save_menu', 'load_menu'
    input_text = ""
    file_list = []
    selected_file_idx = 0
    flash_msg = None
    flash_timer = 0.0
    
    active_stage_idx = 0
    
    running = True
    while running:
        raw_dt = clock.tick(FPS) / 1000.0
        
        # Poll Agent for external commands
        agent.poll()
        
        # Update Simulation
        sim.update(raw_dt)
        
        # Flash Message Timer
        if flash_timer > 0:
            flash_timer -= raw_dt
            if flash_timer <= 0: flash_msg = None

        # --- INPUT HANDLING ---
        events = pygame.event.get()
        for event in events:
            if event.type == pygame.QUIT:
                running = False
            
            # Modal Input Handling
            if ui_mode == 'save_menu':
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE: ui_mode = 'sim'; input_text = ""
                    elif event.key == pygame.K_RETURN:
                        # Save
                        fname = input_text + ".pkl" if not input_text.endswith(".pkl") else input_text
                        # Construct State Dict
                        # Note: We need to serialize the simulation state.
                        # Since we split classes, pickle might have issues dumping 'Simulation' object if we just dump properties.
                        # But here we are dumping a dict of plain objects (lists of Stages, Requests).
                        # Stages and Requests are now in 'models' module.
                        # Pickle should handle this if we load it back with the same code structure.
                        state = {
                            'stages': sim.stages,
                            'requests': sim.requests,
                            'latencies': sim.completed_latencies,
                            'sim_time': sim.sim_time,
                            'rps': sim.current_rps,
                            'key_limit': sim.active_key_limit,
                            'drag': sim.drag_coeff,
                            'comp_spd': sim.compute_speed
                        }
                        success, msg = save_simulation(fname, state)
                        flash_msg = msg; flash_timer = 2.0
                        ui_mode = 'sim'; input_text = ""
                    elif event.key == pygame.K_BACKSPACE: input_text = input_text[:-1]
                    else: input_text += event.unicode
                continue

            if ui_mode == 'load_menu':
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE: ui_mode = 'sim'
                    elif event.key == pygame.K_UP: selected_file_idx = max(0, selected_file_idx - 1)
                    elif event.key == pygame.K_DOWN: selected_file_idx = min(len(file_list) - 1, selected_file_idx + 1)
                    elif event.key == pygame.K_RETURN:
                        if file_list:
                             fname = file_list[selected_file_idx]
                             success, data, msg = load_simulation(fname)
                             if success and data:
                                 sim.stages = data['stages']
                                 sim.requests = data['requests']
                                 sim.completed_latencies = data['latencies']
                                 sim.sim_time = data['sim_time']
                                 sim.current_rps = data['rps']
                                 sim.active_key_limit = data['key_limit']
                                 sim.drag_coeff = data['drag']
                                 sim.compute_speed = data['comp_spd']
                                 
                                 # Re-wire stages
                                 sim.num_stages = len(sim.stages)
                                 flash_msg = msg; flash_timer = 2.0
                             else:
                                 flash_msg = msg; flash_timer = 2.0
                             ui_mode = 'sim'
                continue

            # Simulation Input
            if event.type == pygame.KEYDOWN:
                mods = pygame.key.get_mods()
                is_ctrl = (mods & pygame.KMOD_CTRL)
                is_shift = (mods & pygame.KMOD_SHIFT)

                if event.key == pygame.K_h: show_help = not show_help
                
                # --- GLOBAL CONTROLS ---
                if event.key == pygame.K_UP: sim.current_rps += 1
                if event.key == pygame.K_DOWN: sim.current_rps = max(0, sim.current_rps - 1)
                
                if event.key == pygame.K_RIGHT: sim.sim_speed = round(sim.sim_speed + 0.1, 1)
                if event.key == pygame.K_LEFT: sim.sim_speed = max(0.1, round(sim.sim_speed - 0.1, 1))

                if event.key == pygame.K_k: sim.compute_speed = max(0.1, round(sim.compute_speed - 0.1, 1))
                if event.key == pygame.K_l: sim.compute_speed = round(sim.compute_speed + 0.1, 1)
                
                if event.key == pygame.K_s: sim.saturation_enabled = not sim.saturation_enabled
                if is_shift and event.key == pygame.K_d:  pass 
                
                if event.key == pygame.K_r: sim.reset(); flash_msg = "Reset"; flash_timer = 1.0
                if event.key == pygame.K_x: 
                     for s in sim.stages: s.load_history.clear()
                     sim.completed_latencies.clear()
                     flash_msg = "Hist Cleared"; flash_timer = 1.0

                # --- SAVE / LOAD ---
                if is_ctrl and event.key == pygame.K_s:
                    ui_mode = 'save_menu'
                    input_text = f"save_{int(time.time())}"
                
                if is_ctrl and event.key == pygame.K_l:
                    ui_mode = 'load_menu'
                    # Populate file list
                    files = [f for f in os.listdir('.') if f.endswith('.pkl')]
                    files.sort(key=lambda x: os.path.getmtime(x), reverse=True)
                    file_list = files
                    selected_file_idx = 0

                if event.key == pygame.K_F5:
                     # Quick Save
                     state = {
                            'stages': sim.stages,
                            'requests': sim.requests,
                            'latencies': sim.completed_latencies,
                            'sim_time': sim.sim_time,
                            'rps': sim.current_rps,
                            'key_limit': sim.active_key_limit,
                            'drag': sim.drag_coeff,
                            'comp_spd': sim.compute_speed
                        }
                     success, msg = save_simulation("quicksave.pkl", state)
                     flash_msg = msg; flash_timer = 2.0

                if event.key == pygame.K_F6:
                     # Quick Load
                     success, data, msg = load_simulation("quicksave.pkl")
                     if success and data:
                         sim.stages = data['stages']
                         sim.requests = data['requests']
                         sim.completed_latencies = data['latencies']
                         sim.sim_time = data['sim_time']
                         sim.current_rps = data['rps']
                         sim.active_key_limit = data['key_limit']
                         sim.drag_coeff = data['drag']
                         sim.compute_speed = data['comp_spd']
                         sim.num_stages = len(sim.stages)
                         flash_msg = msg; flash_timer = 2.0
                     else:
                         flash_msg = msg; flash_timer = 2.0

                # --- STAGE CONTROLS ---
                if 0 <= active_stage_idx < len(sim.stages):
                    curr = sim.stages[active_stage_idx]
                    
                    if event.key == pygame.K_TAB:
                        if is_shift: active_stage_idx = (active_stage_idx - 1) % len(sim.stages)
                        else: active_stage_idx = (active_stage_idx + 1) % len(sim.stages)
                    
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

                    # --- CAPACITY / LATENCY GROUP ([ ]) ---
                    if event.key == pygame.K_LEFTBRACKET:
                        if is_shift: curr.work_time = max(0.1, round(float(curr.work_time - 0.1), 1))
                        else: curr.adjust_capacity(-1)
                    if event.key == pygame.K_RIGHTBRACKET:
                        if is_shift: curr.work_time = round(float(curr.work_time + 0.1), 1)
                        else: curr.adjust_capacity(1)

                    # --- BATCH GROUP (- =) ---
                    if event.key == pygame.K_MINUS:
                        if is_shift: curr.batch_window = max(0.0, round(float(curr.batch_window - 0.1), 1))
                        else: curr.batch_max_size = max(1, curr.batch_max_size - 1)
                    if event.key == pygame.K_EQUALS:
                        if is_shift: curr.batch_window = round(float(curr.batch_window + 0.1), 1)
                        else: curr.batch_max_size += 1

                    # --- KEYSPACE CONTROL (9 0) ---
                    if event.key == pygame.K_9: sim.active_key_limit = max(1, sim.active_key_limit - 1)
                    if event.key == pygame.K_0: sim.active_key_limit = min(KEYS_PER_STAGE, sim.active_key_limit + 1)

                    # --- TTL / REFRESH GROUP (6 7) ---
                    if event.key == pygame.K_6:
                        if is_shift: curr.refresh_time = max(0.1, round(float(curr.refresh_time - 0.1), 1))
                        else: curr.ttl = max(0.5, round(float(curr.ttl - 0.5), 1))
                    if event.key == pygame.K_7:
                        if is_shift: curr.refresh_time = round(float(curr.refresh_time + 0.1), 1)
                        else: curr.ttl = round(float(curr.ttl + 0.5), 1)
                    curr.refresh_time = min(curr.refresh_time, curr.ttl)

            elif event.type == pygame.MOUSEBUTTONDOWN:
                if event.button == 1: # Left Click
                    mods = pygame.key.get_mods()
                    is_bust = (mods & pygame.KMOD_SHIFT)
                    # Need Request Class for this. It's not imported directly in main?
                    # Simulation should have a method "spawn_request" or I should import Request if I want to append to sim.requests from main.
                    # sim.requests is a list of Request objects.
                    # I should import Request.
                    pass
                    # FIX: Import Request in main.py, OR add a 'spawn_manual_request' method to Simulation.
                    # For now, I'll update main.py to import Request from models.
                    
                    from models import Request # Local import or top level
                    sim.requests.append(Request(sim.sim_time, lane_idx=0, is_tracer=True, is_bust=is_bust, key_limit=sim.active_key_limit))

                elif event.button == 3: # Right Click -> Lane 1
                    if sim.active_lane_count > 1:
                        mods = pygame.key.get_mods()
                        is_bust = (mods & pygame.KMOD_SHIFT)
                        from models import Request
                        sim.requests.append(Request(sim.sim_time, lane_idx=1, is_tracer=True, is_bust=is_bust, key_limit=sim.active_key_limit))

        # --- DRAWING ---
        draw_sim(screen, sim, fonts, show_help, ui_mode, input_text, file_list, selected_file_idx, flash_msg, active_stage_idx)
        pygame.display.flip()

    pygame.quit()

if __name__ == "__main__":
    main()
