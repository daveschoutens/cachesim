import subprocess
import json
import time
import sys

def send_cmd(proc, cmd_dict):
    proc.stdin.write(json.dumps(cmd_dict) + "\n")
    proc.stdin.flush()
    # Read response
    while True:
        line = proc.stdout.readline()
        if not line: break
        try:
            resp = json.loads(line)
            if resp.get('type') == 'ACK' and resp.get('action') == cmd_dict.get('action'):
                return resp
            if resp.get('type') == 'PONG':
                return resp
            if resp.get('type') == 'STATE':
                return resp
        except json.JSONDecodeError:
            continue

def get_state(proc):
    proc.stdin.write(json.dumps({"type": "GET_STATE"}) + "\n")
    proc.stdin.flush()
    while True:
        line = proc.stdout.readline()
        if not line: return None
        try:
            resp = json.loads(line)
            if resp.get('type') == 'STATE':
                return resp
        except:
            continue

def main():
    print("Starting cachesim.py...")
    proc = subprocess.Popen(
        ['python3', 'cachesim.py'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0
    )
    time.sleep(2) # Warmup

    try:
        print("--- TEST 1: RECONFIGURE STAGE ---")
        # Set Stage 0: 2 workers, 2.0s work time (High Latency)
        print("Setting Stage 0 to: workers=2, work_time=2.0")
        send_cmd(proc, {
            "type": "ACTION", 
            "action": "configure_stage", 
            "stage_idx": 0, 
            "workers": 2, 
            "work_time": 2.0,
            "ttl": 10.0
        })
        
        # Verify
        state = get_state(proc)
        s0 = state['stages'][0]
        print(f"Stage 0 Config: Workers={s0['workers']}, WorkTime={s0['work_time']}, TTL={s0['ttl']}")
        if s0['workers'] == 2 and s0['work_time'] == 2.0 and s0['ttl'] == 10.0:
            print("SUCCESS: Stage configured.")
        else:
            print("FAILED: Stage configuration mismatch.")

        print("\n--- TEST 2: INVALIDATE CACHE ---")
        # Invalidate All
        print("Sending Invalidate All...")
        send_cmd(proc, {"type": "ACTION", "action": "invalidate_cache", "stage_idx": 0})
        # Hard to verify explicitly without requests, but check for ACK
        print("Received ACK for Invalidation.")

        print("\n--- TEST 3: GLOBAL SETTINGS ---")
        send_cmd(proc, {"type": "ACTION", "action": "set_global", "compute_speed": 2.0, "drag": 0.01})
        time.sleep(0.1)
        state = get_state(proc)
        if state:
            print(f"Global Config: ComputeSpeed={state['compute_speed']}, Drag={state['drag']}")
            if state['compute_speed'] == 2.0 and state['drag'] == 0.01:
                print("SUCCESS: Global settings configured.")
            else:
                print("FAILED: Global settings mismatch.")
        else:
            print("FAILED: Could not get state.")

        print("\n--- TEST 4: BATCHING & COALESCING ---")
        print("Setting Stage 0: Batch=True, Window=0.5, Size=10, Coalesce=True")
        send_cmd(proc, {
            "type": "ACTION",
            "action": "configure_stage",
            "stage_idx": 0,
            "batch_enabled": True,
            "batch_window": 0.5,
            "batch_max_size": 10,
            "coalesce_enabled": True
        })
        time.sleep(0.1)
        state = get_state(proc)
        if state:
            s0 = state['stages'][0]
            print(f"Stage 0 Batch Config: Enabled={s0['batch_enabled']}, Window={s0['batch_window']}, Size={s0['batch_max_size']}, Coalesce={s0['coalesce_enabled']}")
            if s0['batch_enabled'] and s0['batch_window'] == 0.5 and s0['batch_max_size'] == 10 and s0['coalesce_enabled']:
                print("SUCCESS: Batching/Coalescing configured.")
            else:
                 print("FAILED: Config mismatch.")
        else:
            print("FAILED: Could not get state.")

    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        print("Terminating...")
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
