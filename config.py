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
