import sqlite3
import json
import logging
# pyrefly: ignore [missing-import]
import numpy as np 
from typing import List, Dict, Any, Optional

logger = logging.getLogger("database")

def get_connection(db_path: str) -> sqlite3.Connection:
    """Создает соединение с базой данных SQLite."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.row_factory = sqlite3.Row  # Позволяет получать результаты по имени столбца
    return conn

def init_db(db_path: str) -> None:
    """Инициализирует таблицы базы данных, если они еще не созданы."""
    logger.info(f"Инициализация базы данных: {db_path}")
    conn = get_connection(db_path)
    cursor = conn.cursor()
    
    # Таблица media
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filepath TEXT UNIQUE,
            filename TEXT,
            filesize INTEGER,
            media_type TEXT,
            creation_date REAL,
            md5_hash TEXT,
            image_hash TEXT,
            status TEXT,
            latitude REAL,
            longitude REAL,
            country TEXT,
            city TEXT
        )
    """)
    
    # Таблица faces
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            bbox TEXT,
            face_embedding BLOB,
            cluster_id TEXT,
            FOREIGN KEY (media_id) REFERENCES media (id) ON DELETE CASCADE
        )
    """)
    
    # Индексы для ускорения поиска
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_filepath ON media(filepath)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_md5 ON media(md5_hash)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_media_status ON media(status)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_faces_media_id ON faces(media_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_faces_cluster_id ON faces(cluster_id)")
    
    conn.commit()
    conn.close()
    logger.info("База данных успешно инициализирована.")

# --- Помощники сериализации эмбеддингов ---
def embedding_to_blob(embedding: List[float]) -> bytes:
    """Преобразует 512-мерный вектор в байты (numpy float32)."""
    arr = np.array(embedding, dtype=np.float32)
    return arr.tobytes()

def blob_to_embedding(blob: bytes) -> List[float]:
    """Восстанавливает вектор из байт."""
    arr = np.frombuffer(blob, dtype=np.float32)
    return arr.tolist()

# --- Операции с файлами media ---

def add_media_file(db_path: str, filepath: str, filename: str, filesize: int, media_type: str, md5_hash: str, status: str, image_hash: Optional[str] = None) -> int:
    """
    Добавляет новый медиа-файл в БД или обновляет существующий, если пути совпадают.
    Возвращает ID записи.
    """
    conn = get_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            INSERT INTO media (filepath, filename, filesize, media_type, md5_hash, image_hash, status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(filepath) DO UPDATE SET
                filename=excluded.filename,
                filesize=excluded.filesize,
                media_type=excluded.media_type,
                md5_hash=excluded.md5_hash,
                image_hash=excluded.image_hash,
                status=excluded.status
        """, (filepath, filename, filesize, media_type, md5_hash, image_hash, status))
        
        # Получаем ID вставленной или обновленной строки
        if cursor.lastrowid:
            row_id = cursor.lastrowid
        else:
            cursor.execute("SELECT id FROM media WHERE filepath=?", (filepath,))
            row_id = cursor.fetchone()[0]
            
        conn.commit()
        return row_id
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при добавлении медиа-файла {filepath}: {e}")
        raise
    finally:
        conn.close()

def get_media_by_hash(db_path: str, md5_hash: str) -> List[Dict[str, Any]]:
    """Возвращает список медиа-файлов с совпадающим хешем (для поиска дубликатов)."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM media WHERE md5_hash=?", (md5_hash,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_media_by_path(db_path: str, filepath: str) -> Optional[Dict[str, Any]]:
    """Возвращает медиа-файл по его пути."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM media WHERE filepath=?", (filepath,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def update_media_metadata(db_path: str, media_id: int, creation_date: Optional[float], 
                          latitude: Optional[float], longitude: Optional[float], 
                          country: Optional[str], city: Optional[str], status: str) -> None:
    """Обновляет извлеченные метаданные файла."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("""
            UPDATE media
            SET creation_date=?, latitude=?, longitude=?, country=?, city=?, status=?
            WHERE id=?
        """, (creation_date, latitude, longitude, country, city, status, media_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при обновлении метаданных для media_id {media_id}: {e}")
        raise
    finally:
        conn.close()

def update_media_status(db_path: str, media_id: int, status: str) -> None:
    """Обновляет статус обработки файла."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE media SET status=? WHERE id=?", (status, media_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка обновления статуса для media_id {media_id}: {e}")
        raise
    finally:
        conn.close()

def get_media_by_status(db_path: str, status: str) -> List[Dict[str, Any]]:
    """Возвращает файлы с определенным статусом."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM media WHERE status=?", (status,))
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

# --- Операции с лицами faces ---

def add_face(db_path: str, media_id: int, bbox: List[int], face_embedding: List[float], cluster_id: Optional[str] = None) -> int:
    """Добавляет информацию о найденном лице."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    bbox_str = json.dumps(bbox)
    emb_blob = embedding_to_blob(face_embedding)
    try:
        cursor.execute("""
            INSERT INTO faces (media_id, bbox, face_embedding, cluster_id)
            VALUES (?, ?, ?, ?)
        """, (media_id, bbox_str, emb_blob, cluster_id))
        conn.commit()
        return cursor.lastrowid
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при сохранении лица для media_id {media_id}: {e}")
        raise
    finally:
        conn.close()

def get_faces_by_media_id(db_path: str, media_id: int) -> List[Dict[str, Any]]:
    """Возвращает все лица, обнаруженные на конкретном фото."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, bbox, cluster_id FROM faces WHERE media_id=?", (media_id,))
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        d = dict(row)
        d["bbox"] = json.loads(d["bbox"])
        result.append(d)
    return result

def get_all_embeddings(db_path: str) -> List[Dict[str, Any]]:
    """Получает все лица для этапа кластеризации (возвращает id, embedding, cluster_id)."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    # Загружаем только непустые эмбеддинги
    cursor.execute("SELECT id, media_id, face_embedding, cluster_id FROM faces")
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        d = dict(row)
        d["face_embedding"] = blob_to_embedding(d["face_embedding"])
        result.append(d)
    return result

def update_face_cluster(db_path: str, face_id: int, cluster_id: str) -> None:
    """Обновляет ID кластера или имя человека для конкретного лица."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE faces SET cluster_id=? WHERE id=?", (cluster_id, face_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при обновлении кластера для лица id {face_id}: {e}")
        raise
    finally:
        conn.close()

def bulk_update_face_cluster_name(db_path: str, old_cluster_id: str, new_cluster_id: str) -> None:
    """Каскадное слияние кластеров (возрастные эпохи и т.д.)."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE faces SET cluster_id=? WHERE cluster_id=?", (new_cluster_id, old_cluster_id))
        conn.commit()
        logger.info(f"Объединены лица с cluster_id '{old_cluster_id}' в новый '{new_cluster_id}'")
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при пакетном обновлении имен кластеров {old_cluster_id} -> {new_cluster_id}: {e}")
        raise
    finally:
        conn.close()

def get_named_faces(db_path: str) -> List[Dict[str, Any]]:
    """Возвращает все размеченные пользователем лица для использования в KNN при инкрементном поиске."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    # Игнорируем Noise, пустышка и неразмеченные (null)
    cursor.execute("""
        SELECT id, face_embedding, cluster_id
        FROM faces
        WHERE cluster_id IS NOT NULL
          AND cluster_id != 'Noise'
          AND cluster_id != 'пустышка'
          AND cluster_id NOT LIKE 'Cluster_%'
    """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d["face_embedding"] = blob_to_embedding(d["face_embedding"])
        result.append(d)
    return result

def get_negative_faces(db_path: str) -> List[Dict[str, Any]]:
    """Возвращает все негативные шаблоны (Noise, пустышка) для фильтрации."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, face_embedding, cluster_id
        FROM faces
        WHERE cluster_id IN ('Noise', 'пустышка')
    """)
    rows = cursor.fetchall()
    conn.close()

    result = []
    for row in rows:
        d = dict(row)
        d["face_embedding"] = blob_to_embedding(d["face_embedding"])
        result.append(d)
    return result

def update_media_image_hash(db_path: str, media_id: int, image_hash: str) -> None:
    """Обновляет перцептивный хеш изображения."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE media SET image_hash=? WHERE id=?", (image_hash, media_id))
        conn.commit()
    except Exception as e:
        conn.rollback()
        logger.error(f"Ошибка при обновлении image_hash для media_id {media_id}: {e}")
        raise
    finally:
        conn.close()

def get_all_image_hashes(db_path: str) -> List[Dict[str, Any]]:
    """Получает все записи с непустыми image_hash для поиска визуальных дубликатов."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT id, filepath, filesize, image_hash, status FROM media WHERE image_hash IS NOT NULL")
    rows = cursor.fetchall()
    conn.close()
    return [dict(row) for row in rows]

def get_faces_by_cluster(db_path: str, cluster_id: str) -> List[Dict[str, Any]]:
    """Возвращает список лиц (id, filepath, bbox) для конкретного кластера."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT f.id, m.filepath, f.bbox 
        FROM faces f 
        JOIN media m ON f.media_id = m.id 
        WHERE f.cluster_id = ?
    """, (cluster_id,))
    rows = cursor.fetchall()
    conn.close()
    
    result = []
    for row in rows:
        d = dict(row)
        d["bbox"] = json.loads(d["bbox"])
        result.append(d)
    return result

def get_unnamed_clusters(db_path: str) -> List[str]:
    """Возвращает список уникальных неименованных кластеров."""
    conn = get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT cluster_id 
        FROM faces 
        WHERE cluster_id LIKE 'Cluster_%'
    """)
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]
