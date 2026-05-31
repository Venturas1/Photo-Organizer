import os
import re
import io
import datetime
import hashlib
import logging
import subprocess
import json
# pyrefly: ignore [missing-import]
from PIL import Image, ImageOps
# pyrefly: ignore [missing-import]
import exifread
# pyrefly: ignore [missing-import]
import pillow_heif
# pyrefly: ignore [missing-import]
import rawpy
# pyrefly: ignore [missing-import]
import imagehash
from tqdm import tqdm
from typing import Dict, Any, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import database
import geocoding

logger = logging.getLogger("media_parser")

# Регистрируем HEIF-декодер в PIL
pillow_heif.register_heif_opener()

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".heic", ".heif", ".cr2", ".nef", ".arw", ".dng"}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".3gp"}

def calculate_md5(filepath: str) -> str:
    """Быстрый расчет MD5-хеша файла чанками по 64 КБ."""
    hash_md5 = hashlib.md5()
    try:
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                hash_md5.update(chunk)
        return hash_md5.hexdigest()
    except Exception as e:
        logger.error(f"Не удалось рассчитать MD5 для {filepath}: {e}")
        raise

def load_image_to_pil(filepath: str, draft_size: Optional[Tuple[int, int]] = None) -> Image.Image:
    """
    Безопасная загрузка изображения в PIL.
    Поддерживает JPG, PNG, HEIC (через pillow_heif), RAW (через rawpy с извлечением превью).
    """
    ext = os.path.splitext(filepath)[1].lower()
    if ext in {".cr2", ".nef", ".arw", ".dng"}:
        with rawpy.imread(filepath) as raw:
            try:
                # Пытаемся извлечь быстрое JPEG-превью из RAW
                thumb = raw.extract_thumb()
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    with Image.open(io.BytesIO(thumb.data)) as img:
                        orig_w, orig_h = img.size
                        try:
                            orientation = img.getexif().get(0x0112)
                            if orientation in (5, 6, 7, 8):
                                original_size = (orig_h, orig_w)
                            else:
                                original_size = (orig_w, orig_h)
                        except Exception:
                            original_size = (orig_w, orig_h)

                        if draft_size:
                            img.draft('RGB', draft_size)
                        img.load()
                        
                        img = ImageOps.exif_transpose(img)
                        img.original_size = original_size
                        return img
                elif thumb.format == rawpy.ThumbFormat.BITMAP:
                    img = Image.fromarray(thumb.data)
                    img = ImageOps.exif_transpose(img)
                    img.original_size = img.size
                    return img
            except rawpy.LibRawNoThumbnailError:
                pass
            # Если превью нет, делаем быстрое полуразмерное декодирование
            rgb = raw.postprocess(use_camera_wb=True, half_size=True)
            img = Image.fromarray(rgb)
            img.original_size = img.size
            return img
    else:
        # Для HEIC и стандартных форматов
        with Image.open(filepath) as img:
            orig_w, orig_h = img.size
            try:
                orientation = img.getexif().get(0x0112)
                if orientation in (5, 6, 7, 8):
                    original_size = (orig_h, orig_w)
                else:
                    original_size = (orig_w, orig_h)
            except Exception:
                original_size = (orig_w, orig_h)

            if draft_size:
                img.draft('RGB', draft_size)
            img.load()
            
            img = ImageOps.exif_transpose(img)
            img.original_size = original_size
            return img

def parse_exif_date(date_str: str) -> Optional[float]:
    """Парсит строку даты из EXIF формата YYYY:MM:DD HH:MM:SS в timestamp."""
    try:
        # Бывает, что EXIF дата содержит лишние пробелы или пустая
        date_str = date_str.strip()
        if not date_str or date_str.startswith("0000:00:00"):
            return None
        dt = datetime.datetime.strptime(date_str[:19], "%Y:%m:%d %H:%M:%S")
        return dt.timestamp()
    except Exception:
        return None

def get_gps_coordinates(tags: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Извлекает координаты GPS (широта, долгота) из EXIF-тегов."""
    def _to_degrees(value) -> float:
        d = float(value.values[0].num) / float(value.values[0].den)
        m = float(value.values[1].num) / float(value.values[1].den)
        s = float(value.values[2].num) / float(value.values[2].den)
        return d + (m / 60.0) + (s / 3600.0)

    lat = None
    lon = None
    try:
        if 'GPS GPSLatitude' in tags and 'GPS GPSLatitudeRef' in tags:
            lat_value = tags['GPS GPSLatitude']
            lat_ref = tags['GPS GPSLatitudeRef'].values
            lat = _to_degrees(lat_value)
            if lat_ref != 'N':
                lat = -lat
                
        if 'GPS GPSLongitude' in tags and 'GPS GPSLongitudeRef' in tags:
            lon_value = tags['GPS GPSLongitude']
            lon_ref = tags['GPS GPSLongitudeRef'].values
            lon = _to_degrees(lon_value)
            if lon_ref != 'E':
                lon = -lon
    except Exception as e:
        logger.warning(f"Ошибка декодирования GPS-данных: {e}")
        
    return lat, lon

def get_video_creation_time(filepath: str) -> Optional[float]:
    """Быстрое извлечение creation_time видео с помощью mutagen или ffprobe."""
    # 1. Пробуем прочитать теги напрямую через mutagen (для mp4, m4v, mov)
    if filepath.lower().endswith(('.mp4', '.m4v', '.mov')):
        try:
            # pyrefly: ignore [missing-import]
            from mutagen.mp4 import MP4
            video = MP4(filepath)
            # В MP4 дата создания обычно лежит в \xa9day (copyright day)
            creation_time = video.get("\xa9day", [None])[0]
            if creation_time:
                # Предотвращаем ошибки парсинга часовых поясов
                clean_time = creation_time.replace("Z", "+00:00")
                try:
                    dt = datetime.datetime.fromisoformat(clean_time)
                    return dt.timestamp()
                except ValueError:
                    # Попробуем распарсить просто YYYY-MM-DD
                    if len(clean_time) >= 10:
                        dt = datetime.datetime.strptime(clean_time[:10], "%Y-%m-%d")
                        return dt.timestamp()
        except Exception as e:
            logger.debug(f"Mutagen не смог прочитать {filepath}: {e}")

    # 2. Если mutagen не сработал или формат не поддерживается, используем ffprobe как резервный вариант
    try:
        cmd = [
            "ffprobe", 
            "-v", "quiet", 
            "-print_format", "json", 
            "-show_format", 
            "-show_streams", 
            filepath
        ]
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
        data = json.loads(result.stdout)
        
        creation_time = None
        if "format" in data and "tags" in data["format"]:
            creation_time = data["format"]["tags"].get("creation_time")
            
        if not creation_time and "streams" in data:
            for stream in data["streams"]:
                if "tags" in stream:
                    creation_time = stream["tags"].get("creation_time")
                    if creation_time:
                        break
                        
        if creation_time:
            # Предотвращаем ошибки парсинга часовых поясов
            clean_time = creation_time.replace("Z", "+00:00")
            dt = datetime.datetime.fromisoformat(clean_time)
            return dt.timestamp()
    except Exception as e:
        logger.warning(f"Не удалось прочитать метаданные ffprobe для {filepath}: {e}")
    return None

def parse_date_from_filename(filename: str) -> Optional[float]:
    """Парсит дату из имени файла (Telegram, WhatsApp и др.)."""
    patterns = [
        # IMG_20210515_143020 / VID_20210515_143020
        r"(?:IMG|VID|PANO|AUD|GIF)_(\d{4})(\d{2})(\d{2})_(\d{2})(\d{2})(\d{2})",
        # 2021-05-15_14-30-20
        r"(\d{4})-(\d{2})-(\d{2})_(\d{2})-(\d{2})-(\d{2})",
        # 2021-05-15-14-30-20
        r"(\d{4})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})",
        # Только дата: 20210515 / 2021-05-15
        r"(\d{4})-(\d{2})-(\d{2})",
        r"(\d{4})(\d{2})(\d{2})"
    ]
    for pattern in patterns:
        match = re.search(pattern, filename)
        if match:
            groups = match.groups()
            try:
                if len(groups) == 6:
                    dt = datetime.datetime(
                        int(groups[0]), int(groups[1]), int(groups[2]),
                        int(groups[3]), int(groups[4]), int(groups[5])
                    )
                else:
                    dt = datetime.datetime(
                        int(groups[0]), int(groups[1]), int(groups[2])
                    )
                # Запобігаємо помилкам з роками поза межами підтримуваного діапазону timestamp на Windows (наприклад, 1274 рік)
                if not (1970 <= dt.year <= 2038):
                    continue
                return dt.timestamp()
            except (ValueError, OSError):
                continue
    return None

def is_screenshot(filename: str, tags: Dict[str, Any], pil_img: Image.Image, config: Dict[str, Any]) -> bool:
    """Детектирует, является ли файл скриншотом, мемом или интернет-мусором."""
    rules = config["screenshot_rules"]
    
    # 1. Проверка по ключевым словам в имени
    filename_lower = filename.lower()
    for kw in rules["keywords"]:
        if kw in filename_lower:
            return True
            
    # 2. Если включена проверка EXIF, смотрим, есть ли данные о камере
    if rules["check_exif"]:
        # Если EXIF пустой или нет производителя/модели камеры, это подозрительно
        has_camera = any(key in tags for key in ["Image Make", "Image Model", "EXIF LensModel"])
        if not has_camera:
            # Проверяем соотношение сторон для мобильных экранов
            w, h = getattr(pil_img, 'original_size', pil_img.size)
            if w > 0 and h > 0:
                aspect_ratio = max(w, h) / min(w, h)
                if rules["aspect_ratio_min"] <= aspect_ratio <= rules["aspect_ratio_max"]:
                    return True
                    
            # Дополнительные проверки на интернет-графику и мусор
            # Если это PNG, WEBP или GIF без метаданных камеры — это графика/скриншот/интернет
            ext = os.path.splitext(filename_lower)[1]
            if ext in [".png", ".webp", ".gif"]:
                return True
                
            # Если картинка слишком маленькая (иконки, мелкий веб-мусор)
            if w > 0 and h > 0 and (w < 350 or h < 350):
                return True
                
            # Проверяем типичные префиксы камеры/мессенджеров.
            # Если имя файла не начинается с них и нет EXIF — это интернет-загрузка или мем.
            camera_prefixes = ["img_", "dsc", "pano", "mvimg", "p_", "c360", "wp_", "sam_", "imag", "gopr", "ydxj", "whatsapp", "telegram", "viber", "photo_"]
            has_camera_prefix = any(filename_lower.startswith(p) for p in camera_prefixes)
            if not has_camera_prefix:
                return True
                    
    return False

# --- СТАДИЯ 1: Сканирование и дедупликация ---

def scan_and_deduplicate(config: Dict[str, Any]) -> None:
    """Рекурсивно сканирует источник, выявляет битые файлы и точные MD5-дубликаты."""
    source_dir = config["source_dir"]
    db_path = config["db_path"]
    
    # Инициализируем БД
    database.init_db(db_path)
    
    logger.info(f"Начало сканирования папки: {source_dir}")
    
    # Собираем все поддерживаемые файлы
    all_files = []
    for root, _, files in os.walk(source_dir):
        for file in files:
            ext = os.path.splitext(file)[1].lower()
            if ext in PHOTO_EXTS or ext in VIDEO_EXTS:
                all_files.append(os.path.join(root, file))
                
    if not all_files:
        print("У вихідній папці не знайдено підходящих медіафайлів.")
        return
        
    print(f"Знайдено медіафайлів для індексації: {len(all_files)}")
    
    # Будемо виводити прогрес-бар в консоль
    for filepath in tqdm(all_files, desc="Індексація та MD5 дедуплікація", unit="файл"):
        try:
            filename = os.path.basename(filepath)
            
            # Проверка на пустые/битые файлы
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                database.add_media_file(db_path, filepath, filename, 0, "photo", "", "CORRUPTED")
                continue
                
            filesize = os.path.getsize(filepath)
            
            # Проверяем, есть ли уже этот файл в БД
            existing_media = database.get_media_by_path(db_path, filepath)
            if existing_media and existing_media["filesize"] == filesize:
                # Файл уже проиндексирован и не менялся, пропускаем его повторное сканирование
                continue
            
            # Определяем медиа тип по расширению
            ext = os.path.splitext(filename)[1].lower()
            media_type = "video" if ext in VIDEO_EXTS else "photo"
            
            # Считаем MD5
            md5_hash = calculate_md5(filepath)
            
            # Проверяем точные копии по MD5 в базе
            existing_copies = database.get_media_by_hash(db_path, md5_hash)
            
            # Отфильтруем самого себя (если файл уже есть в базе по этому же пути)
            exact_duplicates = [f for f in existing_copies if f["filepath"] != filepath]
            
            if exact_duplicates:
                status = "DUPLICATE_MD5"
                logger.info(f"Найден точный дубликат по MD5: {filepath} -> {exact_duplicates[0]['filepath']}")
            else:
                status = "SCANNED"
                
            database.add_media_file(db_path, filepath, filename, filesize, media_type, md5_hash, status)
            
        except Exception as e:
            logger.error(f"Ошибка при первичном сканировании файла {filepath}: {e}")
            filename = os.path.basename(filepath)
            database.add_media_file(db_path, filepath, filename, 0, "photo", "", "CORRUPTED")

# --- СТАДИЯ 2: Извлечение метаданных и фильтрация скриншотов ---

def process_single_file(record: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Обрабатывает один медиа-файл, извлекая метаданные, проверяя скриншоты и считая хеш."""
    filepath = record["filepath"]
    filename = record["filename"]
    media_type = record["media_type"]
    media_id = record["id"]

    creation_date = None
    latitude = None
    longitude = None
    country = None
    city = None
    img_hash_str = None
    status = "METADATA_EXTRACTED"

    try:
        # 1. Дата из имени файла (мессенджеры часто стирают EXIF, имя — приоритетный источник для них)
        creation_date = parse_date_from_filename(filename)

        if media_type == "video":
            # Извлекаем дату видео
            ffprobe_date = get_video_creation_time(filepath)
            if ffprobe_date:
                creation_date = ffprobe_date

            # Если даты нет, используем дату изменения файла
            if not creation_date:
                creation_date = os.path.getmtime(filepath)

        else: # photo
            # Читаем EXIF
            tags = {}
            try:
                with open(filepath, 'rb') as f:
                    tags = exifread.process_file(f, details=False)
            except Exception as e:
                logger.warning(f"Не удалось прочитать EXIF для {filepath}: {e}")

            # Если в EXIF есть дата и она не распарсилась раньше (или приоритет EXIF)
            if 'EXIF DateTimeOriginal' in tags:
                exif_date = parse_exif_date(str(tags['EXIF DateTimeOriginal']))
                if exif_date:
                    creation_date = exif_date

            # Если все еще нет даты, берем системную
            if not creation_date:
                creation_date = os.path.getmtime(filepath)

            # Извлекаем GPS
            latitude, longitude = get_gps_coordinates(tags)

            # Офлайн-геокодинг (получение названий на украинском языке)
            if latitude is not None and longitude is not None:
                country, city = geocoding.geocode(latitude, longitude)

            # Пытаемся загрузить картинку для перцептивного хеша и проверки на скриншот
            try:
                # Используем draft_size для экономии CPU и памяти!
                pil_img = load_image_to_pil(filepath, draft_size=(256, 256))

                # Детекция скриншотов
                if is_screenshot(filename, tags, pil_img, config):
                    status = "SCREENSHOT"
                    logger.info(f"Файл распознан как скриншот/мем: {filepath}")

                # Вычисляем перцептивный хеш (pHash) для поиска визуальных дубликатов
                p_hash = imagehash.phash(pil_img, hash_size=config["duplicates"]["hash_size"])
                img_hash_str = str(p_hash)

            except Exception as e:
                # Если файл не открывается как картинка
                logger.error(f"Не удалось открыть изображение {filepath} для анализа: {e}")
                return {"media_id": media_id, "status": "CORRUPTED"}

        return {
            "media_id": media_id,
            "creation_date": creation_date,
            "latitude": latitude,
            "longitude": longitude,
            "country": country,
            "city": city,
            "status": status,
            "img_hash_str": img_hash_str
        }
    except Exception as e:
        logger.error(f"Критическая ошибка обработки метаданных для {filepath}: {e}")
        return {"media_id": media_id, "status": "CORRUPTED"}


def extract_metadata_and_filter(config: Dict[str, Any]) -> None:
    """Извлекает EXIF, координаты, перцептивные хеши и маркирует скриншоты и визуальные дубликаты."""
    db_path = config["db_path"]
    max_workers = config.get("metadata_threads", 8)
    
    # Получаем файлы со статусом SCANNED (пропускаем уже обработанные и дубликаты MD5)
    files_to_process = database.get_media_by_status(db_path, "SCANNED")
    
    if not files_to_process:
        logger.info("Нет новых файлов для извлечения метаданных.")
        print("Нові файли для вилучення метаданих відсутні.")
        return

    # Предварительно инициализируем reverse_geocoder в одном потоке,
    # чтобы избежать конкурентной загрузки базы городов в параллельных потоках
    try:
        # pyrefly: ignore [missing-import]
        import reverse_geocoder
        reverse_geocoder.search((0.0, 0.0))
    except Exception as e:
        logger.warning(f"Не удалось инициализировать reverse_geocoder: {e}")
        
    print(f"Вилучення метаданих та аналіз для {len(files_to_process)} файлів (потоків: {max_workers})...")
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Отправляем задачи на выполнение в пул
        futures = {executor.submit(process_single_file, record, config): record for record in files_to_process}
        
        # Получаем результаты по мере их завершения и последовательно пишем в БД (чтобы избежать блокировок)
        for future in tqdm(as_completed(futures), total=len(files_to_process), desc="Вилучення метаданих", unit="файл"):
            record = futures[future]
            try:
                res = future.result()
                media_id = res["media_id"]
                status = res.get("status")
                
                if status == "CORRUPTED":
                    database.update_media_status(db_path, media_id, "CORRUPTED")
                else:
                    # Сохраняем в БД
                    database.update_media_metadata(
                        db_path, media_id, 
                        res["creation_date"], 
                        res["latitude"], 
                        res["longitude"], 
                        res["country"], 
                        res["city"], 
                        status
                    )
                    if res["img_hash_str"]:
                        database.update_media_image_hash(db_path, media_id, res["img_hash_str"])
            except Exception as e:
                logger.error(f"Помилка в потоці при обробці {record['filepath']}: {e}")
                
    # Запускаем поиск визуальных дубликатов
    find_and_mark_visual_duplicates(config)

def find_and_mark_visual_duplicates(config: Dict[str, Any]) -> None:
    """Группирует изображения по схожести перцептивного хеша и маркирует дубликаты."""
    db_path = config["db_path"]
    threshold = config["duplicates"]["hash_threshold"]
    
    # Загружаем все изображения с хешами
    records = database.get_all_image_hashes(db_path)
    
    if not records:
        return
        
    print("Аналіз перцептивних хешів на наявність візуальних дублікатів...")
    
    # Преобразуем строковые хеши в объекты imagehash
    hashes = []
    for r in records:
        try:
            hashes.append({
                "id": r["id"],
                "filepath": r["filepath"],
                "filesize": r["filesize"],
                "hash_obj": imagehash.hex_to_hash(r["image_hash"]),
                "status": r["status"]
            })
        except Exception as e:
            logger.error(f"Ошибка конвертации хеша для media_id {r['id']}: {e}")
            
    # Группировка дубликатов
    marked_duplicates = set()
    
    for i in range(len(hashes)):
        if hashes[i]["id"] in marked_duplicates:
            continue
            
        group = [hashes[i]]
        
        for j in range(i + 1, len(hashes)):
            if hashes[j]["id"] in marked_duplicates:
                continue
                
            # Расстояние Хэмминга (разница битов)
            distance = hashes[i]["hash_obj"] - hashes[j]["hash_obj"]
            if distance <= threshold:
                group.append(hashes[j])
                
        if len(group) > 1:
            # Найдена группа визуальных дубликатов!
            # Находим "лучший" файл (с максимальным весом файла)
            group.sort(key=lambda x: x["filesize"], reverse=True)
            master = group[0]
            duplicates = group[1:]
            
            logger.info(f"Найдена группа визуальных дубликатов ({len(group)} шт). Оригинал: {master['filepath']}")
            
            for dup in duplicates:
                marked_duplicates.add(dup["id"])
                # Помечаем дубликат в БД
                database.update_media_status(db_path, dup["id"], "DUPLICATE_VISUAL")
                logger.info(f"Файл {dup['filepath']} помечен как VISUAL DUPLICATE оригинала {master['filepath']}")
