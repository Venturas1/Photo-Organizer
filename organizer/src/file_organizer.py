import os
import shutil
import logging
import datetime
from tqdm import tqdm
from typing import Dict, Any, List, Tuple

import database
import media_parser
import re

logger = logging.getLogger("file_organizer")

def clean_existing_output_duplicates(dest_dir: str) -> int:
    """
    Рекурсивно сканує цільову папку, знаходить файли з суфіксами _1, _2... та видаляє їх,
    якщо в тій же папці лежить файл без суфікса з абсолютно ідентичним MD5-хешем.
    """
    deleted_count = 0
    # Проходимо по дереву папок
    for root, _, files in os.walk(dest_dir):
        for file in files:
            # Шукаємо файли з суфіксом _1, _2... (наприклад, IMG_4533_1.JPG)
            match = re.search(r"_(?P<num>\d+)(?P<ext>\.[^.]+)$", file, re.IGNORECASE)
            if match:
                suffix = f"_{match.group('num')}{match.group('ext')}"
                base_name = file[:-len(suffix)] + match.group('ext')
                base_path = os.path.join(root, base_name)
                dup_path = os.path.join(root, file)
                
                if os.path.exists(base_path):
                    try:
                        # Порівнюємо розмір перед хешуванням
                        if os.path.getsize(base_path) == os.path.getsize(dup_path):
                            base_md5 = media_parser.calculate_md5(base_path)
                            dup_md5 = media_parser.calculate_md5(dup_path)
                            if base_md5 == dup_md5:
                                os.remove(dup_path)
                                logger.info(f"Видалено застарілий дублікат: {dup_path}")
                                deleted_count += 1
                    except Exception as e:
                        logger.error(f"Помилка при перевірці/видаленні дубліката {dup_path}: {e}")
    return deleted_count

def check_already_copied_and_get_path(src_path: str, target_path: str, md5_hash: str) -> Tuple[bool, str]:
    """
    Перевіряє, чи файл вже скопійовано за цим або альтернативними шляхами (з суфіксами _1, _2...).
    Повертає Tuple[is_already_copied, path_to_use].
    """
    if not os.path.exists(target_path):
        return False, target_path

    # Перевіряємо оригінальний шлях
    try:
        if os.path.getsize(target_path) == os.path.getsize(src_path):
            if media_parser.calculate_md5(target_path) == md5_hash:
                return True, target_path
    except Exception:
        pass

    dir_name, file_name = os.path.split(target_path)
    base_name, ext = os.path.splitext(file_name)

    counter = 1
    while True:
        alt_name = f"{base_name}_{counter}{ext}"
        alt_path = os.path.join(dir_name, alt_name)
        if not os.path.exists(alt_path):
            # Знайшли перше вільне ім'я, файл не копіювався
            return False, alt_path
        
        try:
            if os.path.getsize(alt_path) == os.path.getsize(src_path):
                if media_parser.calculate_md5(alt_path) == md5_hash:
                    return True, alt_path
        except Exception:
            pass
            
        counter += 1

def get_unique_filepath(target_path: str) -> str:
    """Повертає унікальний шлях, додаючи суфікс _1, _2 тощо при збігу імен."""
    if not os.path.exists(target_path):
        return target_path

    dir_name, file_name = os.path.split(target_path)
    base_name, ext = os.path.splitext(file_name)

    counter = 1
    while True:
        new_name = f"{base_name}_{counter}{ext}"
        new_path = os.path.join(dir_name, new_name)
        if not os.path.exists(new_path):
            return new_path
        counter += 1

def verify_and_copy(src: str, dest: str, expected_md5: str) -> bool:
    """Копіює файл та звіряє розмір і MD5-хеш."""
    try:
        # Створюємо цільові папки
        os.makedirs(os.path.dirname(dest), exist_ok=True)

        # Копіюємо файл (copy2 зберігає час зміни файлу)
        shutil.copy2(src, dest)

        # Перевіряємо розмір
        if os.path.getsize(src) != os.path.getsize(dest):
            logger.error(f"Невідповідність розмірів після копіювання: {src} -> {dest}")
            return False

        # Перевіряємо MD5
        dest_md5 = media_parser.calculate_md5(dest)
        if dest_md5 != expected_md5:
            logger.error(f"Невідповідність MD5 після копіювання: {src} -> {dest}")
            return False

        return True
    except Exception as e:
        logger.error(f"Помилка при копіюванні файлу {src} в {dest}: {e}")
        return False

def get_location_and_year(creation_date: float, country: str, city: str) -> Tuple[str, str]:
    """Визначає рік та локацію/сезон для файлу."""
    # Визначаємо рік
    if creation_date:
        dt = datetime.datetime.fromtimestamp(creation_date)
        year = str(dt.year)
    else:
        year = "Невідомий рік"

    # Визначаємо геолокацію або сезон
    if country or city:
        if country and city:
            location = f"{country}, {city}"
        elif country:
            location = country
        else:
            location = city
    else:
        # Якщо геокодинг відсутній, визначаємо сезон за датою створення
        if creation_date:
            dt = datetime.datetime.fromtimestamp(creation_date)
            month = dt.month
            if month in (12, 1, 2):
                location = "Зима"
            elif month in (3, 4, 5):
                location = "Весна"
            elif month in (6, 7, 8):
                location = "Літо"
            else:
                location = "Осінь"
        else:
            location = "Без часу та місця"

    return year, location

def get_named_people(faces: List[Dict[str, Any]]) -> List[str]:
    """Витягує список унікальних іменованих людей з облич."""
    named_people = []
    for face in faces:
        cluster_id = face["cluster_id"]
        if cluster_id and cluster_id != "Noise" and cluster_id != "пустышка" and not cluster_id.startswith("Cluster_"):
            named_people.append(cluster_id)
    return list(set(named_people))

def filter_significant_faces(faces: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Фільтрує обличчя для сортування:
    1. Повністю ігнорує Noise (шум / помилкові детекції).
    2. Якщо на фото є чітке велике обличчя (портрет), ігнорує мікро-обличчя на фоні
       (наприклад, обличчя на книгах, плакатах чи випадкових перехожих далеко),
       якщо їхня площа менше 8% від площі найбільшого обличчя.
    """
    # 1. Ігноруємо Noise
    valid_faces = [f for f in faces if f["cluster_id"] != "Noise"]
    if not valid_faces:
        return []

    # 2. Розраховуємо площі для кожного обличчя
    areas = []
    for f in valid_faces:
        bbox = f["bbox"]
        w = abs(bbox[2] - bbox[0])
        h = abs(bbox[3] - bbox[1])
        areas.append(w * h)

    max_area = max(areas)

    # 3. Фільтруємо за відносним розміром
    filtered_faces = []
    for f, area in zip(valid_faces, areas):
        # Якщо найбільше обличчя досить велике (> 15000 пікселів, тобто прибл. 120x120),
        # то будь-яке обличчя менше за 8% від нього вважається незначним тлом.
        if max_area > 15000 and area < max_area * 0.08:
            logger.info(f"Ігноруємо незначне фонове обличчя (площа {area} при максимальній {max_area}) для сортування файлу")
            continue
        filtered_faces.append(f)

    return filtered_faces

def organize_smart(dest_dir: str, filename: str, year: str, location: str,
                   status: str, faces: List[Dict[str, Any]]) -> List[str]:
    """
    Єдина 'Розумна' структура архіву:
    1. 0 облич -> Пейзажі та Архітектура
    2. Тільки 'пустышки' -> Випадкові перехожі
    3. >= 3 облич (група) -> Групові фото
    4. 1-2 відомих обличчя -> Люди/Ім'я/Рік
    """
    dest_paths = []
    
    # Пропускаємо скріншоти та дублікати (вони йдуть у Кошик окремо)
    if status in ("SCREENSHOT", "DUPLICATE_VISUAL"):
        return dest_paths

    # Фільтруємо незначні обличчя та шум
    faces = filter_significant_faces(faces)

    total_faces = len(faces)
    
    # Аналізуємо, хто є на фото
    known_people = set()
    only_strangers = True
    
    for face in faces:
        cid = face["cluster_id"]
        if cid and cid not in ("Noise", "пустышка") and not cid.startswith("Cluster_"):
            # Це відома нам людина
            known_people.add(cid)
            only_strangers = False
        elif cid and cid.startswith("Cluster_"):
            # Це людина, але ми ще не дали їй ім'я (Невідома особа)
            only_strangers = False
            
    known_people = list(known_people)

    # ЛОГІКА СОРТУВАННЯ:
    
    # 1. Природа, Архітектура, Тварини (0 облич)
    if total_faces == 0:
        target_dir = os.path.join(dest_dir, "Пейзажі та Архітектура", year, location)
        dest_paths.append(os.path.join(target_dir, filename))
        
    # 2. Випадкові перехожі (люди є, але ВСІ вони відмічені як 'пустышка' або 'Noise')
    elif only_strangers:
        target_dir = os.path.join(dest_dir, "Випадкові перехожі", year, location)
        dest_paths.append(os.path.join(target_dir, filename))
        
    # 3. Групові фото (3 і більше облич, серед яких є хоча б один наш знайомий)
    elif total_faces >= 3:
        target_dir = os.path.join(dest_dir, "Групові фото", year, location)
        dest_paths.append(os.path.join(target_dir, filename))
        
    # 4. Портрети та парні фото (1-2 обличчя, наші знайомі)
    else:
        if known_people:
            # Копіюємо фото в особисту папку кожної відомої людини, яка є на фото
            for person in known_people:
                target_dir = os.path.join(dest_dir, "Люди", person, year)
                dest_paths.append(os.path.join(target_dir, filename))
        else:
            # На фото 1-2 людини, але ми ще не розмітили їх (Cluster_X)
            target_dir = os.path.join(dest_dir, "Невідомі особи", year, location)
            dest_paths.append(os.path.join(target_dir, filename))

    return dest_paths

def organize_files(config: Dict[str, Any]) -> None:
    """Стадія 5: Копіювання та остаточна організація файлів у розумну структуру."""
    db_path = config["db_path"]
    dest_dir = config["dest_dir"]

    # Спочатку очищуємо старі некоректні дублікати у папці призначення
    removed_dups = clean_existing_output_duplicates(dest_dir)
    if removed_dups > 0:
        logger.info(f"Очищено застарілих дублікатів у папці призначення: {removed_dups}")
        print(f"Очищено застарілих дублікатів у папці призначення: {removed_dups}")

    # Шукаємо файли, готові до копіювання
    conn = database.get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT * FROM media
        WHERE status IN ('FACES_PROCESSED', 'METADATA_EXTRACTED', 'SCREENSHOT', 'DUPLICATE_VISUAL', 'DUPLICATE_MD5')
    """)
    records = cursor.fetchall()
    conn.close()

    if not records:
        logger.info("Немає файлів для організації/копіювання.")
        print("Файли для організації/копіювання відсутні.")
        return

    print(f"Запуск копіювання та організації {len(records)} файлів...")
    print("Використовується єдина 'Розумна' структура архіву.")

    success_count = 0

    for record in tqdm(records, desc="Організація архіву", unit="файл"):
        media_id = record["id"]
        src_path = record["filepath"]
        filename = record["filename"]
        status = record["status"]
        md5_hash = record["md5_hash"]
        creation_date = record["creation_date"]
        country = record["country"]
        city = record["city"]

        # Отримуємо обличчя для цього фото
        faces = database.get_faces_by_media_id(db_path, media_id)

        # Визначаємо рік та локацію
        year, location = get_location_and_year(creation_date, country, city)

        # Формуємо нове ім'я файлу на основі дати створення (ГГГГ.ММ.ДД-ГГ-ХХ)
        if creation_date:
            try:
                dt = datetime.datetime.fromtimestamp(creation_date)
                date_str = dt.strftime("%Y.%m.%d-%H-%M")
                _, ext = os.path.splitext(filename)
                dest_filename = f"{date_str}{ext}"
            except Exception:
                dest_filename = filename
        else:
            dest_filename = filename

        # Збираємо всі цільові шляхи з усіх активних структур
        all_dest_paths = []

        if status in ("SCREENSHOT", "DUPLICATE_VISUAL", "DUPLICATE_MD5"):
            # Скріншоти, меми та дублікати йдуть виключно в Корзину в папці output
            if status == "SCREENSHOT":
                target_dir = os.path.join(dest_dir, "Кошик", "Скріншоти та Меми")
            elif status == "DUPLICATE_VISUAL":
                target_dir = os.path.join(dest_dir, "Кошик", "Візуальні дублікати")
            else:  # DUPLICATE_MD5
                target_dir = os.path.join(dest_dir, "Кошик", "Дублікати MD5")
            all_dest_paths.append(os.path.join(target_dir, dest_filename))
        else:
            # --- ЗАМІНЮЄМО СТАРІ 3 СТРУКТУРИ НА 1 РОЗУМНУ ---
            # Викликаємо нашу нову ідеальну логіку сортування:
            all_dest_paths.extend(
                organize_smart(dest_dir, dest_filename, year, location, status, faces)
            )

        # Виконуємо безпечне копіювання у всі цільові папки
        all_copies_ok = True
        for dest_path in all_dest_paths:
            # Перевіряємо, чи вже існує тотожний файл за цим або альтернативними шляхами
            is_copied, target_path = check_already_copied_and_get_path(src_path, dest_path, md5_hash)
            
            if is_copied:
                logger.info(f"Файл вже існує та є тотожним: {target_path}. Копіювання пропущено.")
                continue

            # Копіюємо з верифікацією
            if not verify_and_copy(src_path, target_path, md5_hash):
                all_copies_ok = False
                logger.error(f"Не вдалося безпечно скопіювати {src_path} в {target_path}")
            else:
                logger.info(f"Файл успішно скопійовано: {src_path} -> {target_path}")

        # Якщо всі копії пройшли успішно, оновлюємо статус в БД на ORGANIZED
        if all_copies_ok and all_dest_paths:
            database.update_media_status(db_path, media_id, "ORGANIZED")
            success_count += 1
        elif not all_dest_paths:
            # Файл не потрапив в жодну структуру (наприклад, "пустышка" відфільтровано)
            database.update_media_status(db_path, media_id, "ORGANIZED")
            success_count += 1

    print(f"Успішно організовано та скопійовано файлів: {success_count} з {len(records)}")
    logger.info(f"Стадія 5 завершена. Успішно перенесено файлів: {success_count}.")
