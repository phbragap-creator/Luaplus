"""Save Game Backup and Restore logic for the LuaTools Millennium plugin."""

import os
import re
import json
import zipfile
import shutil
import datetime
from typing import Any, Dict, List

from logger import logger
from paths import backend_path
from steam_utils import get_game_install_path_response

SAVES_CONFIG_FILE = "saves_config.json"


def _saves_config_path() -> str:
    return backend_path(SAVES_CONFIG_FILE)


def get_saves_config() -> Dict[str, Any]:
    try:
        path = _saves_config_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                return json.load(fh)
    except Exception as exc:
        logger.warn(f"LuaTools: Failed to read saves config: {exc}")
    return {}


def save_saves_config(data: Dict[str, Any]) -> bool:
    try:
        path = _saves_config_path()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        return True
    except Exception as exc:
        logger.warn(f"LuaTools: Failed to write saves config: {exc}")
        return False


def _get_user_dirs() -> Dict[str, str]:
    userprofile = os.environ.get("USERPROFILE", "")
    appdata = os.environ.get("APPDATA", "")
    localappdata = os.environ.get("LOCALAPPDATA", "")

    documents = os.path.join(userprofile, "Documents")
    if not os.path.exists(documents):
        onedrive_docs = os.path.join(userprofile, "OneDrive", "Documents")
        if os.path.exists(onedrive_docs):
            documents = onedrive_docs

    saved_games = os.path.join(userprofile, "Saved Games")

    return {
        "userprofile": userprofile,
        "appdata": appdata,
        "localappdata": localappdata,
        "documents": documents,
        "saved_games": saved_games,
    }


def _get_game_name(appid: int) -> str:
    try:
        from downloads import _get_loaded_app_name, fetch_app_name
        name = _get_loaded_app_name(appid) or fetch_app_name(appid)
        return name or ""
    except Exception:
        return ""


def _get_name_variants(name: str) -> List[str]:
    if not name:
        return []
    variants = [name]

    # Clean up special chars
    clean_name = re.sub(r"[™®©:]", "", name).strip()
    if clean_name != name:
        variants.append(clean_name)

    # Underscores
    with_underscores = clean_name.replace(" ", "_")
    if with_underscores not in variants:
        variants.append(with_underscores)

    # Dashes
    with_dashes = clean_name.replace(" ", "-")
    if with_dashes not in variants:
        variants.append(with_dashes)

    # CamelCase (no spaces)
    no_spaces = clean_name.replace(" ", "")
    if no_spaces not in variants:
        variants.append(no_spaces)

    return variants


def get_save_paths_for_app(appid: int) -> Dict[str, Any]:
    """Returns save game path configuration for a given appid."""
    config = get_saves_config()
    manual_path = config.get(str(appid))

    if manual_path and os.path.exists(manual_path):
        return {
            "success": True,
            "appid": appid,
            "path": manual_path,
            "isManual": True,
        }

    # 1. Prioritize standard Steam Cloud / userdata location for Steam games
    try:
        steam_path = ""
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
            steam_path = winreg.QueryValueEx(key, "SteamPath")[0]
        except Exception:
            pass

        if not steam_path or not os.path.exists(steam_path):
            common_steam_paths = [
                r"C:\Program Files (x86)\Steam",
                r"C:\Program Files\Steam",
                r"D:\Steam",
                r"E:\Steam"
            ]
            for p in common_steam_paths:
                if os.path.exists(p):
                    steam_path = p
                    break

        if steam_path and os.path.exists(steam_path):
            userdata_root = os.path.join(steam_path, "userdata")
            if os.path.exists(userdata_root):
                for user_id in os.listdir(userdata_root):
                    if not user_id.isdigit():
                        continue
                    target_appid_dir = os.path.join(userdata_root, user_id, str(appid))
                    if os.path.exists(target_appid_dir):
                        remote_dir = os.path.join(target_appid_dir, "remote")
                        if os.path.exists(remote_dir) and os.listdir(remote_dir):
                            return {
                                "success": True,
                                "appid": appid,
                                "path": os.path.abspath(remote_dir),
                                "isManual": False,
                            }
                        if os.listdir(target_appid_dir):
                            return {
                                "success": True,
                                "appid": appid,
                                "path": os.path.abspath(target_appid_dir),
                                "isManual": False,
                            }
    except Exception as exc:
        logger.warn(f"LuaTools: Auto-detection inside Steam userdata failed: {exc}")

    # Attempt auto-detection
    name = _get_game_name(appid)
    if not name:
        return {
            "success": False,
            "error": "Game name could not be resolved.",
            "path": "",
            "isManual": False,
        }

    variants = _get_name_variants(name)
    user_dirs = _get_user_dirs()

    # Search folders
    search_roots = [
        user_dirs["localappdata"],
        user_dirs["appdata"],
        user_dirs["documents"],
        user_dirs["saved_games"],
    ]

    for root in search_roots:
        if not root or not os.path.exists(root):
            continue
        for variant in variants:
            target = os.path.join(root, variant)
            if os.path.exists(target) and os.path.isdir(target):
                # Extra heuristic: check if it contains files or subfolders
                if os.listdir(target):
                    return {
                        "success": True,
                        "appid": appid,
                        "path": target,
                        "isManual": False,
                    }

    # Search in game installation path
    try:
        install_res = get_game_install_path_response(appid)
        install_path = (
            install_res.get("path", "")
            if isinstance(install_res, dict)
            else ""
        )
        if install_path and os.path.exists(install_path):
            common_save_folders = ["saves", "save", "savegames", "userdata"]
            for folder in common_save_folders:
                target = os.path.join(install_path, folder)
                if os.path.exists(target) and os.path.isdir(target):
                    return {
                        "success": True,
                        "appid": appid,
                        "path": target,
                        "isManual": False,
                    }
    except Exception as exc:
        logger.warn(f"LuaTools: Auto-detection inside install path failed: {exc}")

    return {
        "success": False,
        "error": "Save path not found. Please set the path manually.",
        "path": "",
        "isManual": False,
    }


def _is_safe_save_path(path: str) -> Dict[str, Any]:
    """Helper to check if a path is a safe save folder location (blocks drive roots and system folders)."""
    if not path:
        return {"safe": False, "error": "Path cannot be empty."}

    abs_path = os.path.abspath(path)
    if not os.path.exists(abs_path):
        return {"safe": False, "error": "Directory does not exist."}

    # Check for drive roots (e.g. "C:\\", "D:\\", or just "C:")
    drive, tail = os.path.splitdrive(abs_path)
    if not tail or tail.strip(os.sep) == "":
        return {"safe": False, "error": "Cannot set the root directory of a drive as the save folder."}

    # Check for system folders
    forbidden_folders = [
        "windows", "program files", "program files (x86)", "system32", "recovery", "msocache", "recycler", "$recycle.bin"
    ]
    path_lower = abs_path.lower()
    
    # EXCEPTION: Allow Steam userdata subfolders even if they reside inside Program Files / Program Files (x86)
    # E.g. C:\Program Files (x86)\Steam\userdata\<SteamID>\<AppID>\remote is completely safe to back up
    is_steam_userdata = False
    steam_userdata_pattern = os.path.join("steam", "userdata").lower()
    if steam_userdata_pattern in path_lower:
        is_steam_userdata = True

    if not is_steam_userdata:
        for folder in forbidden_folders:
            target = os.path.join(drive, os.sep, folder).lower()
            if path_lower == target or path_lower.startswith(target + os.sep):
                return {"safe": False, "error": f"Cannot set system folder '{folder}' or its subdirectories as the save folder."}

    return {"safe": True, "path": abs_path}


def _validate_save_directory_limits(path: str) -> Dict[str, Any]:
    """
    Validates if the target save directory is safe to process by checking cumulative size
    and file count limits to prevent lockups on massive directories.
    """
    safe_check = _is_safe_save_path(path)
    if not safe_check["safe"]:
        return {"safe": False, "error": safe_check["error"]}

    abs_path = safe_check["path"]

    # 100 MB Safety Limit (Steam saves are typically tiny, a few KB to a few MB)
    MAX_TOTAL_SIZE = 100 * 1024 * 1024 
    # 1000 files safety limit
    MAX_FILES = 1000

    total_size = 0
    file_count = 0

    try:
        for root, dirs, files in os.walk(abs_path):
            # Skip folders that look like symlinks/junctions to prevent infinite loops
            dirs_to_keep = []
            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    if not os.path.islink(dir_path):
                        dirs_to_keep.append(d)
                except Exception:
                    pass
            dirs[:] = dirs_to_keep

            for file in files:
                full_path = os.path.join(root, file)
                try:
                    if os.path.islink(full_path):
                        continue

                    file_size = os.path.getsize(full_path)
                    total_size += file_size
                    file_count += 1

                    if total_size > MAX_TOTAL_SIZE:
                        return {
                            "safe": False, 
                            "error": f"The save directory size ({_format_size(total_size)}) exceeds the maximum safe limit of {_format_size(MAX_TOTAL_SIZE)}. "
                                     f"Backup aborted to prevent system lockup."
                        }

                    if file_count > MAX_FILES:
                        return {
                            "safe": False,
                            "error": f"The save directory contains too many files ({file_count} files). "
                                     f"Backup aborted to prevent system lockup."
                        }
                except Exception:
                    pass
    except Exception as exc:
        return {"safe": False, "error": f"Failed to analyze directory: {exc}"}

    return {"safe": True, "path": abs_path, "total_size": total_size, "file_count": file_count}


def set_manual_save_path(appid: int, path: str) -> Dict[str, Any]:
    """Saves a customized manual path override for the app's saves folder."""
    validation = _validate_save_directory_limits(path)
    if not validation["safe"]:
        return {"success": False, "error": validation["error"]}

    abs_path = validation["path"]
    config = get_saves_config()
    config[str(appid)] = abs_path

    if save_saves_config(config):
        return {
            "success": True,
            "appid": appid,
            "path": abs_path,
            "isManual": True,
        }
    return {"success": False, "error": "Failed to save settings."}


def _get_backups_dir(appid: int) -> str:
    user_dirs = _get_user_dirs()
    # Centralized backup directory under Documents/LuaTools_Backups/{appid}
    backups_root = os.path.join(user_dirs["documents"], "LuaTools_Backups")
    app_backups_dir = os.path.join(backups_root, str(appid))
    os.makedirs(app_backups_dir, exist_ok=True)
    return app_backups_dir


def create_save_backup(appid: int) -> Dict[str, Any]:
    """Creates a timestamped ZIP backup of the app's save folder."""
    path_info = get_save_paths_for_app(appid)
    if not path_info.get("success"):
        return {"success": False, "error": path_info.get("error")}

    save_path = path_info.get("path")
    if not save_path or not os.path.exists(save_path):
        return {"success": False, "error": "Save path directory does not exist."}

    # Safety check to prevent massive volumes, infinite loops, or dangerous roots
    validation = _validate_save_directory_limits(save_path)
    if not validation["safe"]:
        return {"success": False, "error": f"Active save path is not safe: {validation['error']}"}

    save_path = validation["path"]

    try:
        backups_dir = _get_backups_dir(appid)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        zip_filename = f"backup_{timestamp}.zip"
        zip_filepath = os.path.join(backups_dir, zip_filename)

        with zipfile.ZipFile(zip_filepath, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for root, _, files in os.walk(save_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, save_path)
                    zip_file.write(full_path, relative_path)

        # Enforce rotation: keep only last 5 backups
        _enforce_backup_rotation(backups_dir)

        size_bytes = os.path.getsize(zip_filepath)
        size_str = _format_size(size_bytes)

        return {
            "success": True,
            "filename": zip_filename,
            "date": datetime.datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "size": size_str,
        }
    except Exception as exc:
        logger.error(f"LuaTools: Failed to create save backup: {exc}")
        return {"success": False, "error": str(exc)}


def _enforce_backup_rotation(backups_dir: str) -> None:
    try:
        files = [
            os.path.join(backups_dir, f)
            for f in os.listdir(backups_dir)
            if f.startswith("backup_") and f.endswith(".zip")
        ]
        files.sort(key=lambda x: os.path.getmtime(x))  # Sort by modification time (oldest first)

        while len(files) > 5:
            oldest = files.pop(0)
            os.remove(oldest)
            logger.log(f"LuaTools: Rotated/deleted oldest backup -> {oldest}")
    except Exception as exc:
        logger.warn(f"LuaTools: Backup rotation failed: {exc}")


def _format_size(bytes_size: int) -> str:
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.1f} KB"
    return f"{bytes_size / (1024 * 1024):.1f} MB"


def list_save_backups(appid: int) -> Dict[str, Any]:
    """Lists all available ZIP backups for the given appid."""
    try:
        backups_dir = _get_backups_dir(appid)
        backups = []

        if os.path.exists(backups_dir):
            for file in os.listdir(backups_dir):
                if file.startswith("backup_") and file.endswith(".zip"):
                    full_path = os.path.join(backups_dir, file)
                    mtime = os.path.getmtime(full_path)
                    date_str = datetime.datetime.fromtimestamp(mtime).strftime("%d/%m/%Y %H:%M:%S")
                    size_bytes = os.path.getsize(full_path)

                    backups.append(
                        {
                            "filename": file,
                            "date": date_str,
                            "size": _format_size(size_bytes),
                            "timestamp": mtime,
                        }
                    )

        # Sort newest first
        backups.sort(key=lambda x: x["timestamp"], reverse=True)

        return {"success": True, "backups": backups}
    except Exception as exc:
        logger.error(f"LuaTools: Failed to list backups: {exc}")
        return {"success": False, "error": str(exc), "backups": []}


def restore_save_backup(appid: int, filename: str) -> Dict[str, Any]:
    """Restores a save backup, taking a safety snapshot first."""
    path_info = get_save_paths_for_app(appid)
    if not path_info.get("success"):
        return {"success": False, "error": path_info.get("error")}

    save_path = path_info.get("path")
    if not save_path or not os.path.exists(save_path):
        return {"success": False, "error": "Active save directory does not exist."}

    # Validate limits for safety snapshot to prevent lockup on massive directories
    validation = _validate_save_directory_limits(save_path)
    if not validation["safe"]:
        return {"success": False, "error": f"Cannot safely backup current save folder before restoring: {validation['error']}"}

    save_path = validation["path"]

    backups_dir = _get_backups_dir(appid)
    backup_filepath = os.path.join(backups_dir, filename)
    if not os.path.exists(backup_filepath):
        return {"success": False, "error": "Backup file not found."}

    safety_filepath = os.path.join(backups_dir, "safety_backup_before_restore.zip")

    try:
        # 1. Take a safety snapshot of the CURRENT saves
        with zipfile.ZipFile(safety_filepath, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for root, _, files in os.walk(save_path):
                for file in files:
                    full_path = os.path.join(root, file)
                    relative_path = os.path.relpath(full_path, save_path)
                    zip_file.write(full_path, relative_path)

        # 2. Delete current files
        for root, dirs, files in os.walk(save_path, topdown=False):
            for file in files:
                os.remove(os.path.join(root, file))
            for dir_name in dirs:
                os.rmdir(os.path.join(root, dir_name))

        # 3. Extract the backup
        with zipfile.ZipFile(backup_filepath, "r") as zip_file:
            zip_file.extractall(save_path)

        # Remove safety snapshot on success
        if os.path.exists(safety_filepath):
            os.remove(safety_filepath)

        return {"success": True}
    except Exception as exc:
        logger.error(f"LuaTools: Failed to restore backup: {exc}")
        # Roll back to the safety snapshot if restore failed
        try:
            if os.path.exists(safety_filepath):
                for root, dirs, files in os.walk(save_path, topdown=False):
                    for file in files:
                        os.remove(os.path.join(root, file))
                    for dir_name in dirs:
                        os.rmdir(os.path.join(root, dir_name))
                with zipfile.ZipFile(safety_filepath, "r") as zip_file:
                    zip_file.extractall(save_path)
                os.remove(safety_filepath)
        except Exception as rollback_exc:
            logger.error(f"LuaTools: Critical failure. Failed to rollback: {rollback_exc}")

        return {"success": False, "error": f"Failed to restore: {exc}"}
