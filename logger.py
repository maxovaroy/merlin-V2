import logging

# Create a logger
logger = logging.getLogger("merlinRoyz")
logger.setLevel(logging.DEBUG)  # Track everything, DEBUG is the lowest level

# Console handler
ch = logging.StreamHandler()
ch.setLevel(logging.DEBUG)

# File handler
fh = logging.FileHandler("bot_debug.log")
fh.setLevel(logging.DEBUG)

# Formatter
formatter = logging.Formatter(
    "[%(asctime)s] [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
ch.setFormatter(formatter)
fh.setFormatter(formatter)

# Add handlers
logger.addHandler(ch)
logger.addHandler(fh)
