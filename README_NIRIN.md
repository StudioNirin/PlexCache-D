# PlexCache - Updated 11/2025
An updated version of the "PlexCache-Refactored" script with various bugfixes and improvements.


### Core Modules

- **`config.py`**: Configuration management with dataclasses for type safety
- **`logging_config.py`**: Logging setup, rotation, and notification handlers
- **`system_utils.py`**: OS detection, path conversions, and file utilities
- **`plex_api.py`**: Plex server interactions and cache management
- **`file_operations.py`**: File moving, filtering, and subtitle operations
- **`plexcache_app.py`**: Main application orchestrator


## Installation

AlienTech42 has already done a really helpful video on the original PlexCache installation, and for now it's the best resource. 
The install process is pretty much the same for PlexCache-R. However there are some settings in the setup.py that
are either in a different place, or are completely removed/altered/added. So don't follow the video religiously!
https://www.youtube.com/watch?v=9oAnJJY8NH0

1. Put the files from this Repo into a known folder on your Unraid server. I use the following:
   ```bash
   /mnt/user/appdata/plexcache/plexcache_app.py
   ```
   I'll keep using this in my examples, but make sure to use your own path.
   
2. Open up the Unraid Terminal, and install dependencies:
```bash
cd ../mnt/user/appdata/plexcache
pip3 install -r requirements.txt
```
Note: You'll need python installed for this to work. There's a community app for that. 

3. Run the setup script to configure PlexCache:
```bash
python3 plexcache_setup.py
```
Each of the questions should pretty much explain themselves, but I'll keep working on them. 
Or I'll add a guide list on here sometime. 

4. Run the main application:
```bash
python3 plexcache_app.py
```
However you wouldn't really want to run it manually every time, and the dependencies will disappear every time you reset the server. 
So I recommend making the following UserScript:
```bash
#!/bin/bash
cd /mnt/user/appdata/plexcache
pip3 install -r requirements.txt
python3 /mnt/user/appdata/plexcache/plexcache_app.py --skip-cache
```
And set it on a cron job to run whenever you want. I run it once a day at midnight ( 0 0 * * * )


### Command Line Options

- `--debug`: Run in debug mode (no files will be moved)
- `--skip-cache`: Skip using cached data and fetch fresh from Plex



## Migration from Original

The refactored version maintained full compatibility with the original.
HOWEVER - This Redux version DOES NOT maintain full compatibility. 
I did make some vague efforts at the start, but there were so many things that didn't work properly that it just wasn't feasible. 
So while the files used are the same, you -will- need to delete your `plexcache_settings.json` and run a new setup to create a new one. 

1. **Different Configuration**: Uses the same `plexcache_settings.json` file, but the fields have changed
2. **Added Functionality**: All original features still exist, but now also work (where possible) for remote users, not just local. 
3. **Same Output**: Logging and notifications work identically
4. **Same Performance**: No performance degradation. Hopefully. Don't quote me on this. 



## Changelog

- **11/25 - Handling of script_folder link**: Old version had a hardcoded link to the script folder instead of using the user-defined setting.
- **11/25 - Adding logic so a 401 error when looking for watched-media doesn't cause breaking errors**: Seems it's only possible to get 'watched files' data from home users and not remote friends, and the 401 error would stop the script working? Added some logic to plex_api.py.
- **11/25 - Ended up totally changing several functions, and adding some new ones, to fix all the issues with remote users and watchlists and various other things**: So the changelog became way too difficult to maintain at this point cos it was just a bunch of stuff. Hence this changing to a new version of PlexCache. 
