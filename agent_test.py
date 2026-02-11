import subprocess
import json
import time
import sys

def main():
    print("Starting cachesim.py...")
    # Run in a way that doesn't open a window if possible, or just ignore the window
    # We use a virtual env python
    proc = subprocess.Popen(
        ['python3', 'cachesim.py'],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=0 # Unbuffered
    )

    try:
        # Give it a second to initialize
        time.sleep(2)

        print("Sending PING...")
        proc.stdin.write(json.dumps({"type": "PING"}) + "\n")
        proc.stdin.flush()
        
        # Read until we get JSON, skipping pygame banner
        start_time = time.time()
        while time.time() - start_time < 5:
            line = proc.stdout.readline()
            print(f"Received: {line.strip()}")
            if "PONG" in line:
                break
        else:
            print("FAILED: Did not receive PONG")
            return

        print("Sending ACTION: set_rps=5...")
        proc.stdin.write(json.dumps({"type": "ACTION", "action": "set_rps", "value": 5}) + "\n")
        proc.stdin.flush()
        
        line = proc.stdout.readline()
        print(f"Received: {line.strip()}")

        print("Sending GET_STATE...")
        proc.stdin.write(json.dumps({"type": "GET_STATE"}) + "\n")
        proc.stdin.flush()

        line = proc.stdout.readline()
        print(f"Received: {line.strip()}")
        state = json.loads(line)
        
        if state['rps'] == 5:
            print("SUCCESS: State reflects updated RPS")
        else:
            print(f"FAILED: RPS is {state['rps']}, expected 5")

    except Exception as e:
        print(f"ERROR: {e}")
    finally:
        print("Terminating...")
        proc.terminate()
        proc.wait()

if __name__ == "__main__":
    main()
