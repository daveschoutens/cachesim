import sys
import json
import select
from typing import Any

class AgentInterface:
    def __init__(self, sim):
        self.sim = sim

    def poll(self):
        if select.select([sys.stdin], [], [], 0.0)[0]:
            try:
                line = sys.stdin.readline()
                if line:
                    try:
                        cmd = json.loads(line.strip())
                        self.handle_command(cmd)
                    except json.JSONDecodeError:
                        pass
            except Exception:
                pass

    def handle_command(self, cmd):
        if cmd['type'] == 'PING':
            self.respond({"type": "PONG"})
        elif cmd['type'] == 'GET_STATE':
            self.respond(self.sim.get_state())
        elif cmd['type'] == 'ACTION':
            action = cmd.get('action')
            self.sim.handle_action(action, cmd)
            self.respond({"type": "ACK", "action": action})

    def respond(self, data):
        print(json.dumps(data))
        sys.stdout.flush()
