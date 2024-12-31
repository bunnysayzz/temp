from pathlib import Path
import config
from pyrogram.types import InputMediaDocument
import pickle, os, random, string, asyncio
from utils.logger import Logger
from datetime import datetime, timezone

logger = Logger(__name__)

cache_dir = Path("./cache")
cache_dir.mkdir(parents=True, exist_ok=True)
drive_cache_path = cache_dir / "drive.data"


def getRandomID():
    global DRIVE_DATA
    while True:
        id = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

        if id not in DRIVE_DATA.used_ids:
            DRIVE_DATA.used_ids.append(id)
            return id


def get_current_utc_time():
    return datetime.now(timezone.utc).strftime("Date - %Y-%m-%d | Time - %H:%M:%S")


class Folder:
    def __init__(self, name: str, path) -> None:
        self.name = name
        self.contents = {}
        if name == "/":
            self.id = "root"
        else:
            self.id = getRandomID()
        self.type = "folder"
        self.trash = False
        self.path = path[:-1] if path[-1] == "/" else path
        self.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.auth_hashes = []


class File:
    def __init__(
        self,
        name: str,
        file_id: int,
        size: int,
        path: str,
    ) -> None:
        self.name = name
        self.type = type
        self.file_id = file_id
        self.id = getRandomID()
        self.size = size
        self.type = "file"
        self.trash = False
        self.path = path[:-1] if path[-1] == "/" else path
        self.upload_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class NewDriveData:
    def __init__(self, contents: dict, used_ids: list) -> None:
        self.contents = contents
        self.used_ids = used_ids
        self.isUpdated = False

    def save(self) -> None:
        try:
            # First save to temporary file
            temp_path = drive_cache_path.with_suffix('.temp')
            with open(temp_path, "wb") as f:
                pickle.dump(self, f)
                f.flush()
                os.fsync(f.fileno())  # Ensure data is written to disk
                
            # Then rename to actual file (atomic operation)
            os.replace(temp_path, drive_cache_path)
            
            self.isUpdated = True
            logger.info("Successfully saved drive data to local cache")
            
        except Exception as e:
            logger.error(f"Error saving drive data: {str(e)}")
            # Remove temporary file if it exists
            try:
                if temp_path.exists():
                    temp_path.unlink()
            except:
                pass
            raise

    def new_folder(self, path: str, name: str) -> None:
        logger.info(f"Creating new folder {name} in {path}")

        folder = Folder(name, path)
        if path == "/":
            directory_folder: Folder = self.contents[path]
            directory_folder.contents[folder.id] = folder
        else:
            paths = path.strip("/").split("/")
            directory_folder: Folder = self.contents["/"]
            for path in paths:
                directory_folder = directory_folder.contents[path]
            directory_folder.contents[folder.id] = folder

        self.save()

    def new_file(self, path: str, name: str, file_id: int, size: int) -> None:
        logger.info(f"Creating new file {name} in {path}")

        file = File(name, file_id, size, path)
        if path == "/":
            directory_folder: Folder = self.contents[path]
            directory_folder.contents[file.id] = file
        else:
            paths = path.strip("/").split("/")
            directory_folder: Folder = self.contents["/"]
            for path in paths:
                directory_folder = directory_folder.contents[path]
            directory_folder.contents[file.id] = file

        self.save()

    def get_directory(
        self, path: str, is_admin: bool = True, auth: str = None
    ) -> Folder:
        folder_data: Folder = self.contents["/"]
        auth_success = False
        auth_home_path = None

        if path != "/":
            path = path.strip("/")

            if "/" in path:
                path = path.split("/")
            else:
                path = [path]

            for folder in path:
                folder_data = folder_data.contents[folder]

                if auth in folder_data.auth_hashes:
                    auth_success = True
                    auth_home_path = (
                        "/" + folder_data.path.strip("/") + "/" + folder_data.id
                    )

        if not is_admin and not auth_success:
            return None

        if auth_success:
            return folder_data, auth_home_path

        return folder_data

    def get_folder_auth(self, path: str) -> None:
        auth = getRandomID()
        folder_data: Folder = self.contents["/"]

        if path != "/":
            path = path.strip("/")

            if "/" in path:
                path = path.split("/")
            else:
                path = [path]

            for folder in path:
                folder_data = folder_data.contents[folder]

        folder_data.auth_hashes.append(auth)
        self.save()
        return auth

    def get_file(self, path) -> File:
        if len(path.strip("/").split("/")) > 0:
            folder_path = "/" + "/".join(path.strip("/").split("/")[:-1])
            file_id = path.strip("/").split("/")[-1]
        else:
            folder_path = "/"
            file_id = path.strip("/")

        folder_data = self.get_directory(folder_path)
        return folder_data.contents[file_id]

    def rename_file_folder(self, path: str, new_name: str) -> None:
        logger.info(f"Renaming {path} to {new_name}")

        if len(path.strip("/").split("/")) > 0:
            folder_path = "/" + "/".join(path.strip("/").split("/")[:-1])
            file_id = path.strip("/").split("/")[-1]
        else:
            folder_path = "/"
            file_id = path.strip("/")
        folder_data = self.get_directory(folder_path)
        folder_data.contents[file_id].name = new_name
        self.save()

    def trash_file_folder(self, path: str, trash: bool) -> None:
        logger.info(f"Trashing {path}")

        if len(path.strip("/").split("/")) > 0:
            folder_path = "/" + "/".join(path.strip("/").split("/")[:-1])
            file_id = path.strip("/").split("/")[-1]
        else:
            folder_path = "/"
            file_id = path.strip("/")
        folder_data = self.get_directory(folder_path)
        folder_data.contents[file_id].trash = trash
        self.save()

    def get_trashed_files_folders(self):
        root_dir = self.get_directory("/")
        trash_data = {}

        def traverse_directory(folder):
            for item in folder.contents.values():
                if item.type == "folder":
                    if item.trash:
                        trash_data[item.id] = item
                    else:
                        # Recursively traverse the subfolder
                        traverse_directory(item)
                elif item.type == "file":
                    if item.trash:
                        trash_data[item.id] = item

        traverse_directory(root_dir)
        return trash_data

    def delete_file_folder(self, path: str) -> None:
        logger.info(f"Deleting {path}")

        if len(path.strip("/").split("/")) > 0:
            folder_path = "/" + "/".join(path.strip("/").split("/")[:-1])
            file_id = path.strip("/").split("/")[-1]
        else:
            folder_path = "/"
            file_id = path.strip("/")

        folder_data = self.get_directory(folder_path)
        del folder_data.contents[file_id]
        self.save()

    def search_file_folder(self, query: str):
        root_dir = self.get_directory("/")
        search_results = {}

        def traverse_directory(folder):
            for item in folder.contents.values():
                if query.lower() in item.name.lower():
                    search_results[item.id] = item
                if item.type == "folder":
                    traverse_directory(item)

        traverse_directory(root_dir)
        return search_results


class NewBotMode:
    def __init__(self, drive_data: NewDriveData) -> None:
        self.drive_data = drive_data

        # Set the current folder to root directory by default
        self.current_folder = "/"
        self.current_folder_name = "/ (root directory)"

    def set_folder(self, folder_path: str, name: str) -> None:
        self.current_folder = folder_path
        self.current_folder_name = name
        self.drive_data.save()


DRIVE_DATA: NewDriveData = None
BOT_MODE: NewBotMode = None


# Function to backup the drive data to telegram
async def backup_drive_data():
    global DRIVE_DATA
    logger.info("Starting backup drive data task")

    while True:
        try:
            await asyncio.sleep(5)  # Check more frequently - every 5 seconds

            if not DRIVE_DATA.isUpdated:
                continue

            logger.info("Backing up drive data to telegram")
            from utils.clients import get_client

            client = get_client()
            time_text = f"📅 **Last Updated :** {get_current_utc_time()} (UTC +00:00)"
            
            # Add retry mechanism for reliability
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    msg = await client.edit_message_media(
                        config.STORAGE_CHANNEL,
                        config.DATABASE_BACKUP_MSG_ID,
                        media=InputMediaDocument(
                            drive_cache_path,
                            caption=f"🔐 **TG Drive Data Backup File**\n\nDo not edit or delete this message. This is a backup file for the tg drive data.\n\n{time_text}",
                        ),
                        file_name="drive.data",
                    )
                    DRIVE_DATA.isUpdated = False
                    try:
                        await msg.pin()
                    except:
                        pass
                    logger.info("Successfully backed up drive data to telegram")
                    break
                except Exception as e:
                    if attempt == max_retries - 1:
                        raise e
                    await asyncio.sleep(1)
                    
        except Exception as e:
            logger.error(f"Backup Error : {str(e)}")
            await asyncio.sleep(5)  # Short delay before retrying on error


async def init_drive_data():
    # auth_hashes attribute is added to all the folders in the drive data if it doesn't exist

    global DRIVE_DATA

    root_dir = DRIVE_DATA.get_directory("/")
    if not hasattr(root_dir, "auth_hashes"):
        root_dir.auth_hashes = []

    def traverse_directory(folder):
        for item in folder.contents.values():
            if item.type == "folder":
                traverse_directory(item)

                if not hasattr(item, "auth_hashes"):
                    item.auth_hashes = []

    traverse_directory(root_dir)

    DRIVE_DATA.save()


async def loadDriveData():
    global DRIVE_DATA, BOT_MODE

    from utils.clients import get_client
    client = get_client()

    max_retries = 3
    for attempt in range(max_retries):
        try:
            msg = await client.get_messages(
                config.STORAGE_CHANNEL, config.DATABASE_BACKUP_MSG_ID
            )
            
            if not msg or not msg.document or msg.document.file_name != "drive.data":
                raise Exception("Invalid or missing drive.data backup on Telegram")

            # Download with timeout
            dl_path = await asyncio.wait_for(
                msg.download(),
                timeout=30  # 30 second timeout
            )

            with open(dl_path, "rb") as f:
                DRIVE_DATA = pickle.load(f)

            logger.info("Successfully loaded drive data from Telegram backup")
            break

        except asyncio.TimeoutError:
            logger.error(f"Timeout downloading drive.data (attempt {attempt + 1}/{max_retries})")
            if attempt == max_retries - 1:
                raise Exception("Failed to download drive.data after multiple attempts")

        except Exception as e:
            logger.error(f"Error loading drive.data (attempt {attempt + 1}/{max_retries}): {str(e)}")
            if attempt == max_retries - 1:
                logger.warning("Creating new drive.data file")
                DRIVE_DATA = NewDriveData({"/": Folder("/", "/")}, [])
                DRIVE_DATA.save()
            await asyncio.sleep(1)

    # For updating the changes in already existing old backup drive.data file
    await init_drive_data()

    # Start Bot Mode
    if config.MAIN_BOT_TOKEN:
        from utils.bot_mode import start_bot_mode
        BOT_MODE = NewBotMode(DRIVE_DATA)
        await start_bot_mode(DRIVE_DATA, BOT_MODE)
