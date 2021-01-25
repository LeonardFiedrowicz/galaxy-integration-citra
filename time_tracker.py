import time

from galaxy.api.types import LocalGame, LocalGameState

class TimeTracker:
    def __init__(self):
        self.games = []
        self.roms = {}
        self.start_time = 0
        self.end_time = 0

    def _set_session_start(self) -> None:
        # Sets the session start to the current time
        self.start_time = time.time()


    def _set_session_end(self) -> None:
        # Sets the session end to the current time
        self.end_time = time.time()


    def _get_session_duration(self) -> int:
        # Returns the duration of the game session in minutes as an int
        return int(round((self.end_time - self.start_time) / 60))