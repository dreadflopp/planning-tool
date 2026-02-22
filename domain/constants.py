"""Visual and layout constants for the planning tool."""

# Base dimensions (scaled by font_size_factor in layout engine)
VISIT_WIDTH = 278
VISIT_HEIGHT = 90
TRAVEL_HEIGHT = 52
EMPTY_HEIGHT = 30
HEADER_HEIGHT = 88
COLUMN_SPACING = 12
OFFICE_TEMPLATE_HEIGHT = 70

# Color constants
COLOR_TRAVEL_BG = "#FFF9C4"       # soft yellow
COLOR_TRAVEL_BORDER = "#F9A825"
COLOR_EMPTY_BG = "#BBDEFB"        # soft blue
COLOR_EMPTY_BORDER = "#1565C0"
COLOR_VISIT_BG = "#FAFAFA"
COLOR_VISIT_BORDER = "#9E9E9E"
COLOR_HEADER_BG = "#ECEFF1"
COLOR_ROUTE_COLUMN_BG = "#F5F5F5"
COLOR_POOL_COLUMN_BG = "#F0F4F8"

COLOR_VISIT_GREEN = "#43A047"
COLOR_VISIT_PINK = "#E91E63"
COLOR_VISIT_BLUE = "#1E88E5"
COLOR_VISIT_RED = "#E53935"
COLOR_VISIT_ORANGE = "#FB8C00"
COLOR_VISIT_YELLOW = "#FDD835"
COLOR_VISIT_BLACK = "#212121"

COLOR_PAIR_HIGHLIGHT = "#FF6F00"   # orange border for paired visit highlight
COLOR_GREYED_OUT = "#BDBDBD"       # when filtered out

# MIME types for drag and drop
MIME_POOL_VISIT = "application/x-pool-visit"
MIME_ROUTE_ENTRY = "application/x-route-entry"
MIME_OFFICE_TEMPLATE = "application/x-office-template"

# DB path (relative to working directory or user home)
DB_FILENAME = "planning_tool.db"

# API
API_USAGE_WARN_THRESHOLD = 9500   # warn at this many calls
API_USAGE_HARD_LIMIT = 10000
