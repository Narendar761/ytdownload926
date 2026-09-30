import os
import re
import time
import asyncio
import aiohttp
from dotenv import load_dotenv
from pyrogram import Client, filters
from pyrogram.types import Message

load_dotenv()

API_ID = int(os.getenv("API_ID", 0))
API_HASH = os.getenv("API_HASH", "")
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

API_URL = "https://green-waterfall-0253.codeofsaladin.workers.dev/api/youtube?url={url}"

app = Client(
    "yt_downloader_bot",
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN
)

# Regex pattern to match all YouTube URLs in a message
YT_REGEX = r"https?://(?:www\.)?(?:youtube\.com|youtu\.be)/[^\s]+"


# --- Helper Functions ---

def humanbytes(size: int) -> str:
    """Format bytes to human-readable format."""
    if not size:
        return "0 B"
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} PB"


def time_formatter(seconds: float) -> str:
    """Format seconds into readable time (e.g., 01m 20s)."""
    minutes, sec = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {sec}s"
    if minutes > 0:
        return f"{minutes}m {sec}s"
    return f"{sec}s"


async def progress_bar(current: int, total: int, status_msg: Message, start_time: float, action_name: str, last_update: dict):
    """Calculates and edits status message with progress bar, speed, and ETA."""
    now = time.time()
    
    # Update Telegram every 3 seconds to avoid FloodWait limits
    if now - last_update.get("time", 0) < 3 and current != total:
        return
    
    last_update["time"] = now
    diff = now - start_time
    
    if diff <= 0:
        return

    percentage = (current * 100 / total) if total > 0 else 0
    speed = current / diff
    eta = ((total - current) / speed) if speed > 0 else 0

    # 10-block progress bar generator
    filled_blocks = int(percentage // 10)
    bar = "█" * filled_blocks + "░" * (10 - filled_blocks)

    text = (
        f"**{action_name}**\n\n"
        f"[{bar}] `{percentage:.1f}%`\n"
        f"⚡ **Speed:** `{humanbytes(speed)}/s`\n"
        f"📊 **Processed:** `{humanbytes(current)}` / `{humanbytes(total)}`\n"
        f"⏳ **ETA:** `{time_formatter(eta)}`"
    )

    try:
        await status_msg.edit_text(text)
    except Exception:
        pass


# --- Bot Command Handlers ---

@app.on_message(filters.command("start") & filters.private)
async def start_handler(client: Client, message: Message):
    await message.reply_text(
        "👋 **Welcome to YouTube Downloader Bot!**\n\n"
        "Send me one or multiple YouTube links (separated by line or space), and I will process them for you."
    )


@app.on_message(filters.regex(YT_REGEX) & filters.private)
async def multi_yt_download_handler(client: Client, message: Message):
    # Extract all YouTube URLs present in the text
    urls = re.findall(YT_REGEX, message.text)
    total_urls = len(urls)

    if not urls:
        return

    status_msg = await message.reply_text(f"🔍 **Found {total_urls} YouTube link(s). Preparing to process...**")

    async with aiohttp.ClientSession() as session:
        for idx, yt_url in enumerate(urls, 1):
            video_path = f"video_{message.id}_{idx}.mp4"
            thumb_path = f"thumb_{message.id}_{idx}.jpg"
            last_update_tracker = {"time": 0}

            try:
                await status_msg.edit_text(f"🔄 **[{idx}/{total_urls}] Querying API for video details...**")
                
                # Fetch Video Data from API
                api_req = API_URL.format(url=yt_url)
                async with session.get(api_req) as resp:
                    if resp.status != 200:
                        await status_msg.edit_text(f"❌ **[{idx}/{total_urls}] API Request failed.**")
                        await asyncio.sleep(2)
                        continue
                    data = await resp.json()

                if not data.get("success") or "result" not in data or "video" not in data["result"]:
                    await status_msg.edit_text(f"❌ **[{idx}/{total_urls}] Invalid API response.**")
                    await asyncio.sleep(2)
                    continue

                video_data = data["result"]["video"]
                title = video_data.get("content", "YouTube Video")
                thumb_url = video_data.get("cover")
                videos_list = video_data.get("videos", [])

                if not videos_list:
                    await status_msg.edit_text(f"❌ **[{idx}/{total_urls}] No download links found.**")
                    await asyncio.sleep(2)
                    continue

                # First Video Link (Quality 1)
                download_url = videos_list[0].get("url")
                quality = videos_list[0].get("qualityLabel", "Standard")

                if not download_url:
                    await status_msg.edit_text(f"❌ **[{idx}/{total_urls}] Missing download link.**")
                    await asyncio.sleep(2)
                    continue

                # 1. Download Video File with Progress
                start_time = time.time()
                async with session.get(download_url) as v_resp:
                    if v_resp.status != 200:
                        await status_msg.edit_text(f"❌ **[{idx}/{total_urls}] Failed to start video download.**")
                        await asyncio.sleep(2)
                        continue

                    total_size = int(v_resp.headers.get("Content-Length", 0))
                    downloaded_size = 0

                    with open(video_path, "wb") as file:
                        async for chunk in v_resp.content.iter_chunked(1024 * 1024):  # 1 MB chunk
                            file.write(chunk)
                            downloaded_size += len(chunk)
                            
                            if total_size > 0:
                                await progress_bar(
                                    current=downloaded_size,
                                    total=total_size,
                                    status_msg=status_msg,
                                    start_time=start_time,
                                    action_name=f"📥 [{idx}/{total_urls}] Downloading Video ({quality})",
                                    last_update=last_update_tracker
                                )

                # 2. Download Thumbnail Image
                has_thumb = False
                if thumb_url:
                    try:
                        async with session.get(thumb_url) as t_resp:
                            if t_resp.status == 200:
                                with open(thumb_path, "wb") as f:
                                    f.write(await t_resp.read())
                                has_thumb = True
                    except Exception as e:
                        print(f"Thumbnail Error: {e}")

                # 3. Upload Video to Telegram with Progress
                upload_start_time = time.time()
                last_update_tracker = {"time": 0}
                caption = f"🎬 **{title}**\n📌 **Quality:** `{quality}`"

                # Define pyrogram progress callback
                async def tg_upload_progress(current, total):
                    await progress_bar(
                        current=current,
                        total=total,
                        status_msg=status_msg,
                        start_time=upload_start_time,
                        action_name=f"📤 [{idx}/{total_urls}] Uploading to Telegram",
                        last_update=last_update_tracker
                    )

                await client.send_video(
                    chat_id=message.chat.id,
                    video=video_path,
                    thumb=thumb_path if has_thumb else None,
                    caption=caption,
                    reply_to_message_id=message.id,
                    progress=tg_upload_progress
                )

            except Exception as e:
                await message.reply_text(f"⚠️ **Error processing link #{idx}:** `{str(e)}`")

            finally:
                # Cleanup local temp files after each loop iteration
                if os.path.exists(video_path):
                    os.remove(video_path)
                if os.path.exists(thumb_path):
                    os.remove(thumb_path)

        await status_msg.delete()


if __name__ == "__main__":
    print("Bot is running...")
    app.run()
    
