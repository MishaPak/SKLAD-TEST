import sqlite3
from datetime import datetime

DB_PATH = "sklad.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    _init_db(conn)
    return conn

def _init_db(conn):
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS animals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT, name TEXT, color TEXT, age INTEGER,
            legs INTEGER DEFAULT 4, ears INTEGER DEFAULT 2, eyes INTEGER DEFAULT 2,
            row INTEGER, col TEXT, added_at TEXT, user_id INTEGER)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            animal_id INTEGER, user_id INTEGER, missing_fields TEXT)
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS archive (
            id INTEGER, type TEXT, name TEXT, color TEXT, age INTEGER,
            legs INTEGER, ears INTEGER, eyes INTEGER,
            row INTEGER, col TEXT, added_at TEXT, removed_at TEXT)
    """)
    conn.commit()

def get_inventory_brief():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT col, row, type, name, color FROM animals")
    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "Склад пуст."

    brief = []
    for r in rows:
        col, row, atype, name, color = r
        name = name if name else "Без имени"
        color = color if color else "неизвестного цвета"
        brief.append(f"{col}{row}: {atype} {name} ({color})")

    return "\n".join(brief)

def get_stats():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM animals WHERE type = 'Кот'")
    cats = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM animals WHERE type = 'Собака'")
    dogs = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM animals")
    occupied = cursor.fetchone()[0]

    total_cells = 100 # A1-J10
    free_cells = total_cells - occupied

    conn.close()

    return (
        f"📊 **Статистика склада:**\n"
        f"🐈 Котов: {cats}\n"
        f"🐕 Собак: {dogs}\n"
        f"📦 Свободных ячеек: {free_cells}/100"
    )

def get_user_tasks(user_id):
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT a.col, a.row, a.type, t.missing_fields
        FROM tasks t
        JOIN animals a ON t.animal_id = a.id
        WHERE t.user_id = ?
    """, (user_id,))

    rows = cursor.fetchall()
    conn.close()

    if not rows:
        return "✅ У вас нет невыполненных задач. Все карточки животных заполнены!"

    tasks = ["📋 **Ваши задачи (заполните пропуски):**"]
    for r in rows:
        col, row, atype, missing = r
        tasks.append(f"📍 Ячейка {col}{row} ({atype}): нужно уточнить {missing}")

    return "\n".join(tasks)

def _get_free_cell(cursor, preferred=None):
    if preferred:
        col = preferred[0].upper()
        row = int(preferred[1:])
        cursor.execute("SELECT 1 FROM animals WHERE col = ? AND row = ?", (col, row))
        if not cursor.fetchone():
            return col, row

    for r in range(1, 11):
        for c in "ABCDEFGHIJ":
            cursor.execute("SELECT 1 FROM animals WHERE col = ? AND row = ?", (c, r))
            if not cursor.fetchone():
                return c, r
    return None, None

def add_animals(animals_list, user_id):
    if not animals_list:
        return "Нечего добавлять."

    conn = get_connection()
    cursor = conn.cursor()

    added_count = 0
    for animal in animals_list:
        atype = animal.get("type")
        if atype not in ["Кот", "Собака"]:
            continue

        col, row = _get_free_cell(cursor)
        if not col:
            conn.close()
            return "Ошибка: на складе нет свободных мест!"

        name = animal.get("name")
        color = animal.get("color")
        age = animal.get("age")
        legs = animal.get("legs", 4)
        ears = animal.get("ears", 2)
        eyes = animal.get("eyes", 2)
        added_at = datetime.now().isoformat()

        cursor.execute("""
            INSERT INTO animals (type, name, color, age, legs, ears, eyes, row, col, added_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (atype, name, color, age, legs, ears, eyes, row, col, added_at, user_id))

        animal_id = cursor.lastrowid

        # Check for missing fields
        missing = []
        if not name or name.lower() == "неизвестно": missing.append("кличку")
        if not color or color.lower() == "неизвестно": missing.append("цвет")
        if not age or age == "Неизвестно": missing.append("возраст")

        if missing:
            cursor.execute("INSERT INTO tasks (animal_id, user_id, missing_fields) VALUES (?, ?, ?)",
                           (animal_id, user_id, ", ".join(missing)))

        added_count += 1

    conn.commit()
    conn.close()

    if added_count > 0:
        return f"✅ Успешно добавлено животных: {added_count}."
    else:
        return "Ни одного подходящего животного (Кот/Собака) не найдено."

def find_and_modify(intent, user_id, action):
    conn = get_connection()
    cursor = conn.cursor()

    target_cell = intent.get("target_cell")
    target_name = intent.get("target_name")

    # Try to find the animal
    if target_cell:
        col = target_cell[0].upper()
        row = int(target_cell[1:])
        cursor.execute("SELECT id, type, name FROM animals WHERE col = ? AND row = ?", (col, row))
    elif target_name:
        cursor.execute("SELECT id, type, name FROM animals WHERE name LIKE ?", (f"%{target_name}%",))
    else:
        conn.close()
        return "Не удалось определить животное для действия."

    found = cursor.fetchone()
    if not found:
        conn.close()
        return "Животное не найдено в указанном месте или по имени."

    animal_id, atype, name = found

    if action == "remove":
        # Check if animal has tasks
        cursor.execute("SELECT 1 FROM tasks WHERE animal_id = ?", (animal_id,))
        if cursor.fetchone():
            conn.close()
            return f"⚠️ Нельзя списать {atype} {name}, так как его карточка заполнена не полностью! Проверьте список задач."

        # Move to archive
        cursor.execute("SELECT * FROM animals WHERE id = ?", (animal_id,))
        a = cursor.fetchone()
        # id, type, name, color, age, legs, ears, eyes, row, col, added_at, user_id
        cursor.execute("""
            INSERT INTO archive (id, type, name, color, age, legs, ears, eyes, row, col, added_at, removed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (a[0], a[1], a[2], a[3], a[4], a[5], a[6], a[7], a[8], a[9], a[10], datetime.now().isoformat()))

        cursor.execute("DELETE FROM animals WHERE id = ?", (animal_id,))
        res = f"✅ {atype} {name} успешно списан со склада."

    elif action == "update":
        animals = intent.get("animals", [])
        if not animals:
            conn.close()
            return "Нет данных для обновления."

        updates = animals[0]
        # Only update what's provided
        fields = []
        params = []
        for key in ["type", "name", "color", "age", "legs", "ears", "eyes"]:
            if key in updates:
                fields.append(f"{key} = ?")
                params.append(updates[key])

        if fields:
            params.append(animal_id)
            cursor.execute(f"UPDATE animals SET {', '.join(fields)} WHERE id = ?", params)

            # Re-check tasks
            cursor.execute("DELETE FROM tasks WHERE animal_id = ?", (animal_id,))
            cursor.execute("SELECT name, color, age FROM animals WHERE id = ?", (animal_id,))
            a = cursor.fetchone()
            missing = []
            if not a[0] or (isinstance(a[0], str) and a[0].lower() == "неизвестно"): missing.append("кличку")
            if not a[1] or (isinstance(a[1], str) and a[1].lower() == "неизвестно"): missing.append("цвет")
            if not a[2] or str(a[2]).lower() == "неизвестно": missing.append("возраст")

            if missing:
                cursor.execute("INSERT INTO tasks (animal_id, user_id, missing_fields) VALUES (?, ?, ?)",
                               (animal_id, user_id, ", ".join(missing)))

            res = f"✅ Данные {atype} обновлены."
        else:
            res = "Изменений не зафиксировано."

    conn.commit()
    conn.close()
    return res
