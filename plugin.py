import sys, asyncio, logging, time, json

import subprocess
import struct
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

import os, os.path

from dataclasses import dataclass

from urllib.parse import parse_qs, urlparse

from galaxy.api.plugin import Plugin, create_and_run_plugin
from galaxy.api.types import Game, LicenseInfo, LicenseType, Authentication, LocalGame, NextStep, GameTime
from galaxy.api.consts import Platform, LocalGameState
from time_tracker import TimeTracker

# Manually override if you dare
roms_path = ""
emulator_path = ""


class AuthenticationHandler(BaseHTTPRequestHandler):
    def _set_headers(self, content_type='text/html'):
        self.send_response(200)
        self.send_header('Content-type', content_type)
        self.end_headers()

    def do_GET(self):
        if "setpath" in self.path:
            self._set_headers()
            parse_result = urlparse(self.path)
            params = parse_qs(parse_result.query)
            global roms_path, emulator_path
            roms_path = params['path'][0]
            emulator_path = params['emulator_path'][0]
            self.wfile.write("<script>window.location=\"/end\";</script>".encode("utf8"))
            return

        self._set_headers()
        self.wfile.write("""
        <!DOCTYPE html>
        <html>
        <head>
            <title>Citra Integration</title>
            <link href="https://fonts.googleapis.com/css?family=Lato:300&display=swap" rel="stylesheet"> 
            <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/bulma/0.7.5/css/bulma.min.css" integrity="sha256-vK3UTo/8wHbaUn+dTQD0X6dzidqc5l7gczvH+Bnowwk=" crossorigin="anonymous" />
            <style>
                @charset "UTF-8";
                html, body {
                    padding: 0;
                    margin: 0;
                    border: 0;
                    background: rgb(40, 39, 42) !important;
                }
                
                html {
                    font-size: 12px;
                    line-height: 1.5;
                    font-family: 'Lato', sans-serif;
                }

                html {
                    overflow: scroll;
                    overflow-x: hidden;
                }
                ::-webkit-scrollbar {
                    width: 0px;  /* Remove scrollbar space */
                    background: transparent;  /* Optional: just make scrollbar invisible */
                }

                .header {
                    background: rgb(46, 45, 48);
                    height: 66px;
                    line-height: 66px;
                    font-weight: 600;
                    text-align: center;
                    vertical-align: middle;
                    padding: 0;
                    margin: 0;
                    border: 0;
                    font-size: 16px;
                    box-sizing: border-box;
                    border-bottom: 1px solid rgba(0, 0, 0, 0.08);
                    color: white !important;
                }
                
                .sub-container {
                    width: 90%;
                    min-width: 200px;
                }
            </style>
        </head>
        <body>
            <div class="header">
                Citra Plugin Configuration
            </div>
            
            <br />
            
            <div class="sub-container container">
                <form method="GET" action="/setpath">
                    <div class="field">
                      <label class="label has-text-light">Games Location</label>
                      <div class="control">
                            <input class="input" name="path" type="text" class="has-text-light" placeholder="Enter absolute Games path">
                      </div>
                    </div>
                    <div class="field">
                      <label class="label has-text-light">Citra Location</label>
                      <div class="control">
                        <input class="input" name="emulator_path" type="text" class="has-text-light" placeholder="Enter absolute Citra path">
                      </div>
                    </div>
                    
                    <div class="field is-grouped">
                      <div class="control">
                        <input type="submit" class="button is-link" value="Enable Plugin" />
                      </div>
                    </div>
                </form>
            </div>
        </body>
        </html>
        """.encode('utf8'))


class AuthenticationServer(threading.Thread):
    def __init__(self, port = 0):
        super().__init__()
        self.path = ""
        server_address = ('localhost', port)
        self.httpd = HTTPServer(server_address, AuthenticationHandler)#partial(AuthenticationHandler, self))
        self.port = self.httpd.server_port

    def run(self):
        self.httpd.serve_forever()


class CitraPlugin(Plugin):
    def __init__(self, reader, writer, token):
        super().__init__(
            Platform.Nintendo3Ds,  # Choose platform from available list
            "0.3",  # Version
            reader,
            writer,
            token
        )
        self.games = []
        self.time_tracker = TimeTracker()
        self.proc = None
        self.running_game = None
        self.server = AuthenticationServer()
        self.server.start()

    def parse_games(self):
        self.games = get_games(roms_path)

    def shutdown(self):
        self.server.httpd.shutdown()

    async def install_game(self, game_id):
        pass

    async def uninstall_game(self, game_id):
        pass

    async def prepare_game_times_context(self, game_ids):
        logging.debug("preparing game time dict")
        return self._get_games_times_dict()

    async def launch_game(self, game_id):
        from os.path import join
        # Find game - lookup table would be good :P
        for game in self.games:
            if game.program_id == game_id:
                self.update_local_game_status(LocalGame(game_id, 2))
                self.proc = subprocess.Popen([emulator_path + "/citra-qt.exe", game.path])
                self.running_game = game_id
                self.time_tracker._set_session_start()
                break
        return

    def tick(self):
        try:
            #logging.debug("polling status: " + str(poll))
            if self.proc.poll() is not None:
                logging.debug("game closed")
                self.update_local_game_status(LocalGame(self.running_game, 1))
                self.time_tracker._set_session_end()
                session_duration = self.time_tracker._get_session_duration()
                logging.debug("game time: "+str(session_duration)+" minutes")
                last_time_played = int(time.time())
                self._update_game_time(self.running_game, session_duration, last_time_played)
                self.proc = None
                self.running_game = None
        except AttributeError:
            pass

    async def get_game_time(self, game_id, context):
        game_time = context.get(game_id)
        return game_time

    def _update_game_time(self, game_id, session_duration, last_time_played) -> None:
        ''' Returns None 
        
        Update the game time of a single game
        '''
        try:
            base_dir = os.path.dirname(os.path.realpath(__file__))
            game_times_path = "{}/3ds_game_times.json".format(base_dir)

            with open(game_times_path, encoding="utf-8") as game_times_file:
                data = json.load(game_times_file)

            data[game_id]["time_played"] = data.get(game_id).get("time_played") + session_duration
            data[game_id]["last_time_played"] = last_time_played

            with open(game_times_path, "w", encoding="utf-8") as game_times_file:
                json.dump(data, game_times_file, indent=4)

            self.update_game_time(GameTime(game_id, data.get(game_id).get("time_played"), last_time_played))

        except FileNotFoundError:
            logging.error("game times file not found")
            pass

    async def _update_all_game_times(self) -> None:
        await asyncio.sleep(60) # Leave time for Galaxy to fetch games before updating times
        loop = asyncio.get_running_loop()
        new_game_times = await loop.run_in_executor(None, self._get_games_times_dict)
        for game_time in new_game_times:
            self.update_game_time(new_game_times[game_time])

    def _get_games_times_dict(self) -> dict:
        ''' Returns a dict of GameTime objects
        
        Creates and reads the game_times.json file
        '''
        base_dir = os.path.dirname(os.path.realpath(__file__))
        data = {}
        game_times = {}
        path = "{}/3ds_game_times.json".format(base_dir)
        
        # Check if the file exists, otherwise create it with defaults
        if not os.path.exists(path):
            logging.debug("no game times file, creating new one")
            for game in self.games:
                data[game.program_id] = { "name": game.game_title, "time_played": 0, "last_time_played": None }

            with open(path, "w", encoding="utf-8") as game_times_file:
                json.dump(data, game_times_file, indent=4)
        
        # Now read it and return the game times
        with open(path, encoding="utf-8") as game_times_file:
            parsed_game_times_file = json.load(game_times_file)

        for entry in parsed_game_times_file:
            game_id = entry
            time_played = parsed_game_times_file.get(entry).get("time_played")
            last_time_played = parsed_game_times_file.get(entry).get("last_time_played")
            game_times[game_id] = GameTime(game_id, time_played, last_time_played)

        return game_times

    def finish_login(self):
        some_dict = dict()
        some_dict["roms_path"] = roms_path
        some_dict["emulator_path"] = emulator_path
        self.store_credentials(some_dict)

        self.parse_games()
        return Authentication(user_id="a_high_quality_citra_user", user_name=roms_path)

    # implement methods
    async def authenticate(self, stored_credentials=None):
        global roms_path, emulator_path
        # See if we have the path in the cache
        if len(roms_path) == 0 and stored_credentials is not None and "roms_path" in stored_credentials:
            roms_path = stored_credentials["roms_path"]

        if len(emulator_path) == 0 and stored_credentials is not None and "emulator_path" in stored_credentials:
            emulator_path = stored_credentials["emulator_path"]

        if len(roms_path) == 0 or len(emulator_path) == 0:
            PARAMS = {
                "window_title": "Configure Citra Plugin",
                "window_width": 400,
                "window_height": 300,
                "start_uri": "http://localhost:" + str(self.server.port),
                "end_uri_regex": ".*/end.*"
            }
            return NextStep("web_session", PARAMS)

        return self.finish_login()

    async def pass_login_credentials(self, step, credentials, cookies):
        return self.finish_login()

    async def get_owned_games(self):
        owned_games = []
        for game in self.games:
            license_info = LicenseInfo(LicenseType.OtherUserLicense, None)
            owned_games.append(Game(game_id=game.program_id, game_title=game.game_title, dlcs=None,
                        license_info=license_info))
        logging.debug("owned games: "+str(owned_games))
        return owned_games

    async def get_local_games(self):
        local_games = []
        for game in self.games:
            local_game = LocalGame(game.program_id, LocalGameState.Installed)
            local_games.append(local_game)
        return local_games


@dataclass
class NCCHGame():
    program_id: str
    game_title: str
    path: str


def probe_game(path):
    with open(path, 'rb') as f:
        print("Reading:", path)
        f.seek(0x100)
        if f.read(4) != b'NCSD':
            print(path, "doesn't have a NCSD partition table")
            return None

        # Read partition table
        print("Found NCSD partition table")
        f.seek(0x120)
        partition_entry = struct.unpack('ii', f.read(8))
        ncch_offset = partition_entry[0] * 0x200
        ncch_size = partition_entry[1] * 0x200
        print("Game data partition offset:", ncch_offset)
        print("Game data partition size:", ncch_size)

        # Read program ID
        f.seek(ncch_offset + 0x150)
        program_id = f.read(10).decode('ascii')
        print("Program ID:", program_id)

        # Read ExeFS Region Offset
        f.seek(ncch_offset + 0x1A0)
        exefs_offset = struct.unpack('i', f.read(4))[0] * 0x200
        exefs_abs_offset = ncch_offset + exefs_offset
        print("Logo region:", exefs_offset)
        print("Logo absolute pointer:", exefs_abs_offset)

        # Read files
        f.seek(exefs_abs_offset)
        files = dict()
        for i in range(10):
            file_name = f.read(8).decode('ascii').replace('\0', '')
            if len(file_name) == 0:
                continue
            file_offset = struct.unpack('i', f.read(4))[0] + exefs_abs_offset + 0x200  # header offset
            file_size = struct.unpack('i', f.read(4))[0]
            print("Found file:", file_name, "at", file_offset)
            files[file_name] = file_offset

        # Get icon
        if "icon" not in files:
            print(path, "missing exefs://logo")
            return None

        icon_offset = files["icon"]
        f.seek(icon_offset)

        if f.read(4) != b'SMDH':
            print(path, "has invalid SMDH file")
            return

        f.seek(icon_offset + 0x8)

        # Read application title structs
        title_structs = []
        for i in range(12):
            short_desc = f.read(0x80).decode("utf-16").replace('\0', '')
            long_desc = f.read(0x100).decode("utf-16").replace('\0', '').replace('\n', ' ').replace('  ', ' ')
            publisher = f.read(0x80).decode("utf-16").replace('\0', '')
            title_structs.append(long_desc)

        # Check if English title is valid
        title = ""
        if len(title_structs[1]) > 0:
            title = title_structs[1]
        else:
            print("No English title for", path, "- using Japanese")
            title = title_structs[0]

        print(path, "=", title, "(", program_id, ")")
        return NCCHGame(program_id=program_id, game_title=title, path=path)


def get_files_in_dir(path):
    from os.path import isfile, join
    from os import walk
    files = walk(path)
    games_path = []
    for root, dirs, files in walk(path):
        for file in files:
            games_path.append(join(root, file))
    return games_path

def get_games(path):
    games_path = get_files_in_dir(path)
    games = []
    for game_path in games_path:
        game = probe_game(game_path)
        if game is not None:
            games.append(game)
    return games


def main():
    create_and_run_plugin(CitraPlugin, sys.argv)


# run plugin event loop
if __name__ == "__main__":
    main()
