import os
import shutil
import logging
from typing import Dict, Any, List
# pyrefly: ignore [missing-import]
import numpy as np
# pyrefly: ignore [missing-import]
from PIL import Image
# pyrefly: ignore [missing-import]
import hdbscan

import database
import face_processor
import media_parser

logger = logging.getLogger("clustering")

def match_incremental_faces(db_path: str, threshold: float = 0.75, noise_threshold: float = 0.75) -> int:
    """
    Векторне порівняння (косинусна схожість) нових облич із вже розміченими.
    Порівнює всі обличчя зі статусом cluster_id IS NULL із базою шаблонів.
    Також фільтрує обличчя, схожі на "Noise" та "пустышка" (негативні шаблони).
    Порівняння проводиться в один конкурентний прохід, обираючи найкращий збіг.
    """
    # Отримуємо позитивні шаблони (іменовані особи)
    named_faces = database.get_named_faces(db_path)

    # Отримуємо негативні шаблони (Noise та пустышка)
    negative_faces = database.get_negative_faces(db_path)

    if not named_faces and not negative_faces:
        logger.info("Розмічені раніше шаблони відсутні в БД. Пропуск інкрементного зіставлення.")
        return 0

    conn = database.get_connection(db_path)
    cursor = conn.cursor()
    # Порівнюємо тільки нові обличчя
    cursor.execute("SELECT id, face_embedding FROM faces WHERE cluster_id IS NULL")
    unnamed_rows = cursor.fetchall()
    conn.close()

    if not unnamed_rows:
        return 0

    # Об'єднуємо всі шаблони в один список
    templates = []
    for f in named_faces:
        emb = database.blob_to_embedding(f["face_embedding"]) if isinstance(f["face_embedding"], bytes) else f["face_embedding"]
        templates.append({
            "embedding": emb,
            "label": f["cluster_id"],
            "is_negative": False
        })
    for f in negative_faces:
        emb = database.blob_to_embedding(f["face_embedding"]) if isinstance(f["face_embedding"], bytes) else f["face_embedding"]
        templates.append({
            "embedding": emb,
            "label": f["cluster_id"],
            "is_negative": True
        })

    # Перетворюємо ембеддінги на матриці numpy
    U = np.array([database.blob_to_embedding(r["face_embedding"]) for r in unnamed_rows], dtype=np.float32)
    unnamed_ids = [r["id"] for r in unnamed_rows]

    # Нормалізуємо нові обличчя
    U_norms = np.linalg.norm(U, axis=1, keepdims=True)
    U_norms[U_norms == 0] = 1.0
    U_normalized = U / U_norms

    # Матриця шаблонів
    T = np.array([t["embedding"] for t in templates], dtype=np.float32)
    T_norms = np.linalg.norm(T, axis=1, keepdims=True)
    T_norms[T_norms == 0] = 1.0
    T_normalized = T / T_norms

    # Матриця схожості (M x N)
    similarity_matrix = np.dot(U_normalized, T_normalized.T)

    matched_count = 0
    noise_filtered_count = 0

    for i, face_id in enumerate(unnamed_ids):
        best_idx = np.argmax(similarity_matrix[i])
        best_score = similarity_matrix[i, best_idx]
        matched_template = templates[best_idx]

        target_threshold = noise_threshold if matched_template["is_negative"] else threshold

        if best_score >= target_threshold:
            if matched_template["is_negative"]:
                # Обличчя схоже на Noise/пустышка - переносимо в Noise
                database.update_face_cluster(db_path, face_id, "Noise")
                logger.info(f"Обличчя id {face_id} автоматично відфільтровано як Noise (схожість {best_score:.4f} з шаблоном '{matched_template['label']}')")
                noise_filtered_count += 1
            else:
                # Обличчя розпізнано як відому особу
                matched_name = matched_template["label"]
                database.update_face_cluster(db_path, face_id, matched_name)
                logger.info(f"Обличчя id {face_id} автоматично розпізнано як '{matched_name}' (схожість {best_score:.4f})")
                matched_count += 1

    if noise_filtered_count > 0:
        print(f"Інкрементний запуск: відфільтровано як Noise: {noise_filtered_count}")
    if matched_count > 0:
        print(f"Інкрементний запуск: автоматично розпізнано облич: {matched_count}")
    return matched_count

def run_hdbscan_clustering(config: Dict[str, Any]) -> None:
    """Групує обличчя без імені за допомогою алгоритму HDBSCAN."""
    db_path = config["db_path"]

    # Вибираємо для кластеризації ТІЛЬКИ нові обличчя (cluster_id IS NULL)
    # НЕ включаємо Noise та Cluster_% - вони вже оброблені!
    conn = database.get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, face_embedding
        FROM faces
        WHERE cluster_id IS NULL
    """)
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        logger.info("Немає облич для кластеризації.")
        return

    min_cluster_size = config.get("duplicates", {}).get("min_cluster_size", 3)

    # Якщо облич менше, ніж мінімальний розмір кластера, всі вони шум
    if len(rows) < min_cluster_size:
        logger.info("Недостатньо облич для формування бодай одного кластера. Усі обличчя позначено як Noise.")
        for r in rows:
            database.update_face_cluster(db_path, r["id"], "Noise")
        return

    # Перетворюємо на масив ембеддінгів
    X = np.array([database.blob_to_embedding(r["face_embedding"]) for r in rows], dtype=np.float32)
    face_ids = [r["id"] for r in rows]

    print(f"Кластеризація {len(rows)} облич методом HDBSCAN...")

    # Ініціалізація HDBSCAN
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=1,
        metric='euclidean'
    )
    labels = clusterer.fit_predict(X)

    # Генеруємо унікальні номери кластерів, щоб не конфліктувати з існуючими
    conn = database.get_connection(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT cluster_id FROM faces WHERE cluster_id LIKE 'Cluster_%'")
    existing_clusters = cursor.fetchall()
    conn.close()

    existing_nums = set()
    for row in existing_clusters:
        cluster_id = row[0]
        try:
            num = int(cluster_id.split('_')[1])
            existing_nums.add(num)
        except (IndexError, ValueError):
            pass

    # Знаходимо максимальний номер та починаємо з наступного
    max_num = max(existing_nums) if existing_nums else -1

    # Маппінг HDBSCAN міток на нові унікальні номери
    label_mapping = {}
    next_cluster_num = max_num + 1

    # Записуємо мітки кластерів у базу даних
    for face_id, label in zip(face_ids, labels):
        if label == -1:
            cluster_id = "Noise"
        else:
            if label not in label_mapping:
                label_mapping[label] = next_cluster_num
                next_cluster_num += 1
            cluster_id = f"Cluster_{label_mapping[label]}"
        database.update_face_cluster(db_path, face_id, cluster_id)

    num_clusters = len(set(labels) - {-1})
    logger.info(f"HDBSCAN знайшов {num_clusters} унікальних кластерів облич.")

def deduplicate_cluster_faces(db_path: str, similarity_threshold: float = 0.95) -> int:
    """
    Видаляє дублікати облич всередині кожного кластера.
    Якщо два обличчя в одному кластері мають косинусну схожість >= threshold,
    залишаємо тільки одне (з меншим id).
    """
    # Отключено: физическое удаление лиц приводит к тому, что фотографии с единственным лицом
    # теряют его и сортируются как "Пейзажи".
    return 0

    # Отримуємо всі кластери (крім NULL, Noise та пустышка)
    cursor.execute("""
        SELECT DISTINCT cluster_id 
        FROM faces 
        WHERE cluster_id IS NOT NULL 
          AND cluster_id != 'Noise' 
          AND cluster_id != 'пустышка'
    """)
    clusters = [row[0] for row in cursor.fetchall()]
    conn.close()

    total_removed = 0

    for cluster_id in clusters:
        # Отримуємо всі обличчя цього кластера
        conn = database.get_connection(db_path)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, face_embedding
            FROM faces
            WHERE cluster_id = ?
            ORDER BY id ASC
        """, (cluster_id,))
        faces = cursor.fetchall()
        conn.close()

        if len(faces) <= 1:
            continue

        # Перетворюємо на numpy масив
        embeddings = np.array([database.blob_to_embedding(f["face_embedding"]) for f in faces], dtype=np.float32)
        face_ids = [f["id"] for f in faces]

        # Нормалізуємо
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        embeddings_normalized = embeddings / norms

        # Матриця схожості
        similarity_matrix = np.dot(embeddings_normalized, embeddings_normalized.T)

        # Знаходимо дублікати
        to_remove = set()
        for i in range(len(face_ids)):
            if face_ids[i] in to_remove:
                continue
            for j in range(i + 1, len(face_ids)):
                if face_ids[j] in to_remove:
                    continue
                if similarity_matrix[i, j] >= similarity_threshold:
                    # Видаляємо обличчя з більшим id (залишаємо старіше)
                    to_remove.add(face_ids[j])
                    logger.info(f"Дублікат знайдено в кластері '{cluster_id}': face_id {face_ids[j]} схоже на {face_ids[i]} (схожість {similarity_matrix[i, j]:.4f})")

        # Видаляємо дублікати з БД
        if to_remove:
            conn = database.get_connection(db_path)
            cursor = conn.cursor()
            for face_id in to_remove:
                cursor.execute("DELETE FROM faces WHERE id=?", (face_id,))
            conn.commit()
            conn.close()
            total_removed += len(to_remove)
            logger.info(f"Видалено {len(to_remove)} дублікатів з кластера '{cluster_id}'")

    if total_removed > 0:
        print(f"Дедуплікація: видалено {total_removed} дублікатів облич всередині кластерів")

    return total_removed

def interactive_naming(config: Dict[str, Any]) -> None:
    """Запускає інтерактивний діалог у GUI для присвоєння імен кластерам."""
    db_path = config["db_path"]
    
    # Отримуємо всі неіменовані кластери з БД
    unnamed_clusters = database.get_unnamed_clusters(db_path)
    
    if not unnamed_clusters:
        print("Немає нових кластерів для іменування.")
        return
        
    print(f"\nЗапуск GUI для розмітки {len(unnamed_clusters)} неіменованих груп людей...")
    
    # Імпортуємо та запускаємо наш GUI
    from gui_app import FaceLabelingApp
    # pyrefly: ignore [missing-import]
    from PyQt6.QtWidgets import QApplication
    import sys
    
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
        
    window = FaceLabelingApp(db_path, unnamed_clusters)
    window.show()
    app.exec() # Код зупиниться тут, поки користувач не розмітить все і не закриє вікно

def cluster_and_name_faces(config: Dict[str, Any]) -> None:
    """Стадія 4 конвеєра: інкрементний запуск, кластеризація HDBSCAN та іменування."""
    db_path = config["db_path"]

    # 1. Спочатку інкрементне зіставлення (векторний пошук по розмічених шаблонах)
    match_incremental_faces(db_path)

    # 2. Обличчя, що залишилися нерозпізнаними, групуємо HDBSCAN
    run_hdbscan_clustering(config)

    # 3. Дедуплікація облич всередині кластерів
    deduplicate_cluster_faces(db_path)

    # 4. Інтерактивний CLI-діалог іменування нових кластерів
    interactive_naming(config)
