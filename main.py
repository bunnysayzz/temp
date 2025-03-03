from utils.downloader import (
    download_file,
    get_file_info_from_url,
)
import asyncio
from pathlib import Path
from contextlib import asynccontextmanager
import aiofiles
from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Form, Response, Cookie
from fastapi.responses import FileResponse, JSONResponse
from config import ADMIN_PASSWORD, MAX_FILE_SIZE, STORAGE_CHANNEL
from utils.clients import initialize_clients
from utils.directoryHandler import getRandomID
from utils.extra import auto_ping_website, convert_class_to_dict, reset_cache_dir
from utils.streamer import media_streamer
from utils.uploader import start_file_uploader
from utils.logger import Logger
import urllib.parse
from typing import Optional
import time
import os


# Startup Event
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Reset the cache directory, delete cache files
    reset_cache_dir()

    # Initialize the clients
    await initialize_clients()

    # Start the website auto ping task
    asyncio.create_task(auto_ping_website())

    # Add this line
    asyncio.create_task(cleanup_cache())

    yield


app = FastAPI(docs_url=None, redoc_url=None, lifespan=lifespan)
logger = Logger(__name__)


@app.get("/")
async def home_page():
    return FileResponse("website/home.html")


@app.get("/stream")
async def home_page():
    return FileResponse("website/VideoPlayer.html")


@app.get("/static/{file_path:path}")
async def static_files(file_path):
    if "apiHandler.js" in file_path:
        with open(Path("website/static/js/apiHandler.js")) as f:
            content = f.read()
            content = content.replace("MAX_FILE_SIZE__SDGJDG", str(MAX_FILE_SIZE))
        return Response(content=content, media_type="application/javascript")
    return FileResponse(f"website/static/{file_path}")


@app.get("/file")
async def dl_file(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    path = request.query_params["path"]
    file = DRIVE_DATA.get_file(path)
    return await media_streamer(STORAGE_CHANNEL, file.file_id, file.name, request)


# Api Routes


@app.post("/api/checkPassword")
async def check_password(request: Request, response: Response):
    data = await request.json()
    if data["pass"] == ADMIN_PASSWORD:
        response.set_cookie(
            key="admin_auth",
            value=ADMIN_PASSWORD,
            httponly=True,
            samesite="strict",
            secure=True  # Enable for HTTPS
        )
        return JSONResponse({"status": "ok"})
    return JSONResponse({"status": "Invalid password"})


@app.post("/api/createNewFolder")
async def api_new_folder(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"createNewFolder {data}")
    folder_data = DRIVE_DATA.get_directory(data["path"]).contents
    for id in folder_data:
        f = folder_data[id]
        if f.type == "folder":
            if f.name == data["name"]:
                return JSONResponse(
                    {
                        "status": "Folder with the name already exist in current directory"
                    }
                )

    DRIVE_DATA.new_folder(data["path"], data["name"])
    return JSONResponse({"status": "ok"})


@app.post("/api/getDirectory")
async def api_get_directory(
    request: Request,
    admin_auth: Optional[str] = Cookie(None)
):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()
    
    # Check auth cookie instead of password field
    if admin_auth == ADMIN_PASSWORD:
        is_admin = True
    else:
        is_admin = False

    auth = data.get("auth")
    logger.info(f"getFolder {data}")

    try:
        if data["path"] == "/trash":
            data = {"contents": DRIVE_DATA.get_trashed_files_folders()}
            folder_data = convert_class_to_dict(data, isObject=False, showtrash=True)

        elif "/search_" in data["path"]:
            query = urllib.parse.unquote(data["path"].split("_", 1)[1])
            data = {"contents": DRIVE_DATA.search_file_folder(query)}
            folder_data = convert_class_to_dict(data, isObject=False, showtrash=False)

        elif "/share_" in data["path"]:
            path = data["path"].split("_", 1)[1]
            folder_data, auth_home_path = DRIVE_DATA.get_directory(path, is_admin, auth)
            auth_home_path = auth_home_path.replace("//", "/") if auth_home_path else None
            folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)
            return JSONResponse(
                {"status": "ok", "data": folder_data, "auth_home_path": auth_home_path}
            )

        else:
            folder_data = DRIVE_DATA.get_directory(data["path"], is_admin)
            if not folder_data:
                return JSONResponse({"status": "error", "message": "Access denied"})
            folder_data = convert_class_to_dict(folder_data, isObject=True, showtrash=False)
            
        return JSONResponse({"status": "ok", "data": folder_data, "auth_home_path": None})
        
    except Exception as e:
        logger.error(f"Error getting directory: {str(e)}")
        return JSONResponse({"status": "error", "message": str(e)})


SAVE_PROGRESS = {}


@app.post("/api/upload")
async def upload_file(
    file: UploadFile = File(...),
    path: str = Form(...),
    password: str = Form(...),
    id: str = Form(...),
    total_size: str = Form(...),
):
    global SAVE_PROGRESS

    if password != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    total_size = int(total_size)
    SAVE_PROGRESS[id] = ("running", 0, total_size)

    ext = file.filename.lower().split(".")[-1]

    cache_dir = Path("./cache")
    cache_dir.mkdir(parents=True, exist_ok=True)
    file_location = cache_dir / f"{id}.{ext}"

    file_size = 0

    async with aiofiles.open(file_location, "wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            SAVE_PROGRESS[id] = ("running", file_size, total_size)
            file_size += len(chunk)
            if file_size > MAX_FILE_SIZE:
                await buffer.close()
                file_location.unlink()
                raise HTTPException(
                    status_code=400,
                    detail=f"File size exceeds {MAX_FILE_SIZE} bytes limit",
                )
            await buffer.write(chunk)

    SAVE_PROGRESS[id] = ("completed", file_size, file_size)

    asyncio.create_task(
        start_file_uploader(file_location, id, path, file.filename, file_size)
    )

    return JSONResponse({"id": id, "status": "ok"})


@app.post("/api/getSaveProgress")
async def get_save_progress(request: Request):
    global SAVE_PROGRESS

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"getUploadProgress {data}")
    try:
        progress = SAVE_PROGRESS[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except:
        return JSONResponse({"status": "not found"})


@app.post("/api/getUploadProgress")
async def get_upload_progress(request: Request):
    from utils.uploader import PROGRESS_CACHE

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"getUploadProgress {data}")

    try:
        progress = PROGRESS_CACHE[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except:
        return JSONResponse({"status": "not found"})


@app.post("/api/cancelUpload")
async def cancel_upload(request: Request):
    from utils.uploader import STOP_TRANSMISSION
    from utils.downloader import STOP_DOWNLOAD

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"cancelUpload {data}")
    STOP_TRANSMISSION.append(data["id"])
    STOP_DOWNLOAD.append(data["id"])
    return JSONResponse({"status": "ok"})


@app.post("/api/renameFileFolder")
async def rename_file_folder(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"renameFileFolder {data}")
    DRIVE_DATA.rename_file_folder(data["path"], data["name"])
    return JSONResponse({"status": "ok"})


@app.post("/api/trashFileFolder")
async def trash_file_folder(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"trashFileFolder {data}")
    DRIVE_DATA.trash_file_folder(data["path"], data["trash"])
    return JSONResponse({"status": "ok"})


@app.post("/api/deleteFileFolder")
async def delete_file_folder(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"deleteFileFolder {data}")
    DRIVE_DATA.delete_file_folder(data["path"])
    return JSONResponse({"status": "ok"})


@app.post("/api/getFileInfoFromUrl")
async def getFileInfoFromUrl(request: Request):

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"getFileInfoFromUrl {data}")
    try:
        file_info = await get_file_info_from_url(data["url"])
        return JSONResponse({"status": "ok", "data": file_info})
    except Exception as e:
        return JSONResponse({"status": str(e)})


@app.post("/api/startFileDownloadFromUrl")
async def startFileDownloadFromUrl(request: Request):
    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"startFileDownloadFromUrl {data}")
    try:
        id = getRandomID()
        asyncio.create_task(
            download_file(data["url"], id, data["path"], data["filename"], data["singleThreaded"])
        )
        return JSONResponse({"status": "ok", "id": id})
    except Exception as e:
        return JSONResponse({"status": str(e)})


@app.post("/api/getFileDownloadProgress")
async def getFileDownloadProgress(request: Request):
    from utils.downloader import DOWNLOAD_PROGRESS

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"getFileDownloadProgress {data}")

    try:
        progress = DOWNLOAD_PROGRESS[data["id"]]
        return JSONResponse({"status": "ok", "data": progress})
    except:
        return JSONResponse({"status": "not found"})


@app.post("/api/getFolderShareAuth")
async def getFolderShareAuth(request: Request):
    from utils.directoryHandler import DRIVE_DATA

    data = await request.json()

    if data["password"] != ADMIN_PASSWORD:
        return JSONResponse({"status": "Invalid password"})

    logger.info(f"getFolderShareAuth {data}")

    try:
        auth = DRIVE_DATA.get_folder_auth(data["path"])
        return JSONResponse({"status": "ok", "auth": auth})
    except:
        return JSONResponse({"status": "not found"})


@app.get("/api/thumbnail")
async def get_thumbnail(path: str):
    from utils.directoryHandler import DRIVE_DATA
    from utils.thumbnail import generate_thumbnail
    
    try:
        file = DRIVE_DATA.get_file(path)
        thumbnail = await generate_thumbnail(file)
        return Response(content=thumbnail, media_type="image/jpeg")
    except Exception as e:
        logger.error(f"Thumbnail generation error: {e}")
        return Response(status_code=404)


async def cleanup_cache():
    """Periodically clean up the cache directory"""
    while True:
        try:
            cache_dir = Path("./cache")
            current_time = time.time()
            
            for file_path in cache_dir.iterdir():
                try:
                    # Skip important files
                    if (file_path.suffix in ['.session', '.session-journal'] or 
                        file_path.name == 'drive.data'):
                        continue
                        
                    # Remove files older than 1 hour
                    if current_time - file_path.stat().st_mtime > 3600:
                        os.remove(file_path)
                        logger.info(f"Cleaned up old cache file: {file_path}")
                except Exception as e:
                    logger.error(f"Error cleaning up {file_path}: {e}")
                    
        except Exception as e:
            logger.error(f"Cache cleanup error: {e}")
            
        await asyncio.sleep(1800)  # Run every 30 minutes
